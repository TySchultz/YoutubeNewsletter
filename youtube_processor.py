import os
import json
import re
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta
from functools import partial
import requests
import yt_dlp
import concurrent.futures
from openai import OpenAI
from groq import Groq
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')

class YouTubeProcessor:
    def __init__(self, openai_api_key: str, groq_api_key: str, postmark_config: dict, max_workers: int = 10):
        """
        Initialize the YouTubeProcessor.
        :param openai_api_key: API key for OpenAI.
        :param groq_api_key: API key for Groq (used for transcription).
        :param postmark_config: Dictionary with Postmark config {server_token, from_email, to_email}.
        :param max_workers: Maximum number of threads for parallel operations.
        """
        self.openai_client = OpenAI(api_key=openai_api_key)
        self.groq_client = Groq(api_key=groq_api_key)
        self.postmark_config = postmark_config
        self.max_workers = max_workers
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_file = os.path.join(self.base_dir, 'processed_videos.json')
        self.transcript_dir = os.path.join(self.base_dir, 'transcripts')
        self.audio_dir = os.path.join(self.base_dir, 'audio_files')

        os.makedirs(self.transcript_dir, exist_ok=True)
        os.makedirs(self.audio_dir, exist_ok=True)

        self.processed_videos = self._load_processed_videos()
        self.http_session = requests.Session()

    def _load_processed_videos(self) -> dict:
        """Load the record of processed videos from JSON file."""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    logging.warning("Invalid processed_videos.json format. Starting fresh.")
                    return {}
                return data
            except json.JSONDecodeError:
                logging.warning("Could not decode processed_videos.json. Starting fresh.")
                return {}
        return {}

    def _save_processed_videos(self, data: Optional[dict] = None) -> None:
        """
        Save the record of processed videos to JSON file with a backup.
        If data is given, it replaces the current self.processed_videos in the file.
        """
        if data is not None:
            self.processed_videos = data

        if not self.processed_videos:
            return

        backup_file = f"{self.data_file}.backup"
        if os.path.exists(self.data_file):
            try:
                os.replace(self.data_file, backup_file)
            except Exception as e:
                logging.warning(f"Failed to create backup: {str(e)}")

        try:
            with open(self.data_file, 'w') as f:
                json.dump(self.processed_videos, f, indent=2)
        except Exception as e:
            logging.error(f"Error saving processed videos: {str(e)}")
            if os.path.exists(backup_file):
                os.replace(backup_file, self.data_file)
        else:
            if os.path.exists(backup_file):
                os.remove(backup_file)

    def is_video_processed(self, video_id: str) -> bool:
        """Check if a video has already been processed."""
        return video_id in self.processed_videos

    def get_latest_videos(self, channel_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Get the latest videos from a channel/handle, restricted to the last 3 days
        through 1 day in the future.
        """
        three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
        one_day_future = datetime.now(timezone.utc) + timedelta(days=1)

        ydl_opts = {
            'extract_flat': False,
            'force_generic_extractor': False,
            'playlistend': limit,
            'dateafter': three_days_ago.strftime('%Y%m%d'),
            'datebefore': one_day_future.strftime('%Y%m%d'),
            'quiet': True
        }

        if channel_id.startswith('@'):
            channel_url = f'https://www.youtube.com/{channel_id}/videos'
        else:
            channel_url = f'https://www.youtube.com/channel/{channel_id}/videos'
        logging.info(f"Fetching videos for: {channel_url}")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(channel_url, download=False)
                videos = result.get('entries', [])
                filtered_videos = []
                for video in videos:
                    try:
                        upload_date = self._extract_video_date(video)
                        if not upload_date:
                            continue
                        if three_days_ago <= upload_date <= one_day_future:
                            filtered_videos.append({
                                'id': video['id'],
                                'title': video['title'],
                                'upload_date': upload_date.strftime('%Y%m%d')
                            })
                    except Exception as e:
                        logging.warning(f"Could not process metadata from {video.get('title', 'Unknown')}: {str(e)}")
                logging.info(f"Found {len(filtered_videos)} video(s) within the time window for {channel_id}")
                return filtered_videos
        except Exception as e:
            logging.error(f"Error fetching videos for channel {channel_id}: {str(e)}")
            return []

    @staticmethod
    def _extract_video_date(video_data: dict) -> Optional[datetime]:
        """
        Helper to extract a datetime from various fields in the yt-dlp video data.
        """
        candidates = [
            ('upload_date', '%Y%m%d'),
            ('release_date', '%Y%m%d'),
            ('timestamp', None),
            ('published_at', None),
        ]
        for field, date_format in candidates:
            dt_value = video_data.get(field)
            if dt_value:
                try:
                    if field in ('upload_date', 'release_date'):
                        return datetime.strptime(dt_value, date_format).replace(tzinfo=timezone.utc)
                    elif field == 'timestamp':
                        return datetime.fromtimestamp(dt_value, tz=timezone.utc)
                    elif field == 'published_at':
                        return datetime.fromisoformat(dt_value.replace('Z', '+00:00'))
                except Exception:
                    pass
        return None

    def download_audio(self, video_id: str, title: str) -> Optional[str]:
        """Download audio in the lowest quality available for transcription."""
        output_template = os.path.join(self.audio_dir, f'{video_id}')
        final_path = f"{output_template}.m4a"

        ydl_opts = {
            'format': 'bestaudio[abr<50]/worstaudio/worst',
            'outtmpl': output_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '9',
            }],
            'prefer_ffmpeg': True,
            'keepvideo': False,
            'quiet': True,
            'postprocessor_args': [
                '-ar', '8000',
                '-ac', '1',
                '-b:a', '8k'
            ]
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logging.info(f"Downloading lowest quality audio for: {title}")
                ydl.download([f'https://www.youtube.com/watch?v={video_id}'])
            if os.path.exists(final_path):
                return final_path
            logging.error(f"Audio file not found at {final_path}")
            return None
        except Exception as e:
            logging.error(f"Error downloading audio for {title}: {str(e)}")
            return None

    def transcribe_audio(self, audio_path: str) -> Optional[str]:
        """Transcribe audio file using Groq's API."""
        try:
            if not os.path.exists(audio_path):
                logging.error(f"Audio file not found at {audio_path}")
                return None
            with open(audio_path, "rb") as audio_file:
                transcription = self.groq_client.audio.transcriptions.create(
                    file=(audio_path, audio_file.read()),
                    model="distil-whisper-large-v3-en",
                    response_format="verbose_json"
                )
            return transcription.text
        except Exception as e:
            logging.error(f"Error transcribing audio {audio_path}: {str(e)}")
            return None
        finally:
            try:
                if os.path.exists(audio_path):
                    os.remove(audio_path)
            except Exception as e:
                logging.warning(f"Failed to clean up audio file {audio_path}: {str(e)}")

    def summarize_transcript(self, transcript: str, title: str) -> Optional[str]:
        """
        Summarize the transcript using OpenAI.
        """
        try:
            prompt = f"""# Prompt for Newsletter Summary Generation

You are tasked with creating a concise newsletter summary of a YouTube video transcript. Please format your response in markdown and follow this specific structure:

1. Create a clear, informative title that captures the main topic (use H1 #)
2. Write a single paragraph summary (4-5 sentences) that:
   - Introduces the main topic/announcement
   - Explains its significance
   - Highlights key features or innovations
   - Provides context within the industry/market
3. Include a "Key Points" section (use H3 ###) with:
   - 3-5 bullet points maximum
   - Focus on the most important facts, specs, or data
   - Keep each point to one line
   - End with any pending information (like price or release date)

Style guidelines:
- Keep the overall summary under 150 words
- Use clear, professional language
- Format in markdown
- Focus on information that would interest a general business/tech audience

_______
Example: 

# Samsung's Project Mujan: The First Android-Powered Mixed Reality Headset

Samsung and Google have joined forces to unveil Project Mujan, the first VR headset running on Android XR. Set to launch in 2024, this Vision Pro competitor stands out not for its similar aesthetics to Apple's headset, but for its deep integration with Google's ecosystem. The device showcases impressive AI capabilities through Gemini integration, full access to the Google Play Store, and innovative features like real-world "circle to search." While the display quality sits just behind the Vision Pro, Project Mujan's focus on software accessibility and AI assistance points to a future where mixed reality becomes more intuitive and user-friendly.

### Key Points:
* First headset to run Android XR, with full Google Play Store access
* Features Gemini AI integration for voice control and navigation
* Includes removable USB-C battery pack for flexible power options
* Launch price yet to be announced

_________
Please create a summary based on the following transcript:

{transcript}"""

            completion = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=7000
            )
            return completion.choices[0].message.content.strip()
        except Exception as e:
            logging.error(f"Error summarizing transcript for {title}: {e}")
            return None

    def create_bullet_points(self, transcript: str, title: str) -> Optional[str]:
        """
        Convert transcript into detailed bullet points using OpenAI.
        """
        try:
            prompt = f"""Please analyze this transcript and break it down into as many clear, detailed bullet points as possible. Each bullet point should:
- Capture a single distinct idea, fact, or statement
- Be clear and self-contained
- Preserve specific details, numbers, and quotes
- Follow chronological order of the video
- Include timestamps if present in the transcript

Please format each bullet point with a dash (-) and ensure the output is clean and readable.

Transcript to analyze:

{transcript}"""

            completion = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=7000
            )
            return completion.choices[0].message.content.strip()
        except Exception as e:
            logging.error(f"Error creating bullet points for {title}: {e}")
            return None

    def process_single_video(self, channel_id: str, video: dict) -> Optional[dict]:
        """Process one video from audio download to transcription and summarization."""
        video_id = video['id']
        title = video['title']

        if self.is_video_processed(video_id):
            logging.info(f"Skipping already processed video: {title}")
            return None

        logging.info(f"Processing video: {title}")
        channel_transcript_dir = os.path.join(self.transcript_dir, channel_id)
        os.makedirs(channel_transcript_dir, exist_ok=True)

        thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        audio_path = self.download_audio(video_id, title)
        if not audio_path:
            return None

        try:
            transcript = self.transcribe_audio(audio_path)
            if not transcript:
                return None

            transcript_path = os.path.join(channel_transcript_dir, f'{video_id}_transcript.txt')
            with open(transcript_path, 'w', encoding='utf-8') as f:
                f.write(transcript)

            # Create bullet points from transcript
            bullet_points = self.create_bullet_points(transcript, title)
            if not bullet_points:
                return None

            bullet_points_path = os.path.join(channel_transcript_dir, f'{video_id}_bullet_points.txt')
            with open(bullet_points_path, 'w', encoding='utf-8') as f:
                f.write(bullet_points)

            # Use bullet points to create the final summary
            summary = self.summarize_transcript(bullet_points, title)
            if not summary:
                return None

            summary_path = os.path.join(channel_transcript_dir, f'{video_id}_summary.txt')
            with open(summary_path, 'w', encoding='utf-8') as f:
                f.write(summary)

            return {
                'video_id': video_id,
                'channel_id': channel_id,
                'title': title,
                'thumbnail_url': thumbnail_url,
                'video_url': video_url,
                'processed_date': datetime.now(timezone.utc).isoformat(),
                'transcript_path': transcript_path,
                'bullet_points_path': bullet_points_path,
                'summary_path': summary_path,
                'summary': summary
            }
        except Exception as e:
            logging.error(f"Error processing video {title}: {str(e)}")
            return None
        finally:
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except Exception as ex:
                    logging.warning(f"Failed to clean up audio file {audio_path}: {str(ex)}")

    def process_channel(self, channel_id: str) -> List[dict]:
        """
        Fetch the latest videos for a channel, process them concurrently,
        and return a list of processed videos for that channel.
        """
        logging.info(f"Processing channel: {channel_id}")
        videos = self.get_latest_videos(channel_id)
        if not videos:
            logging.info(f"No new videos found for channel: {channel_id}")
            return []

        processed_results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_video = {
                executor.submit(self.process_single_video, channel_id, video): video
                for video in videos
            }
            for future in concurrent.futures.as_completed(future_to_video):
                video = future_to_video[future]
                try:
                    result = future.result()
                    if result:
                        processed_results.append(result)
                except Exception as e:
                    logging.error(f"Error processing video {video.get('title', 'Unknown')}: {str(e)}")
        return processed_results

    def _send_processing_summary(self, processed_videos: List[dict]) -> None:
        """
        Sends an email summary of all processed videos for the current batch.
        This uses both text and HTML versions.
        """
        if not processed_videos:
            logging.info("No videos to summarize; skipping email.")
            return

        date_str = datetime.now().strftime('%B %d, %Y')
        subject = f"YouTube Update - {date_str}"

        text_body = f"YouTube Update - {date_str}\n\n"
        for vid in processed_videos:
            text_body += f"Channel: {vid['channel_id']}\n"
            text_body += vid['summary'] + "\n\n---\n\n"

        html_body = self._build_html_email(processed_videos, date_str)
        self.send_email_notification(subject, text_body, html_body)

    def _build_html_email(self, processed_videos: List[dict], date_str: str) -> str:
        """
        Internal helper to build an HTML email from processed videos.
        Uses OpenAI to reformat the summary from Markdown to HTML if possible.
        """
        # Read the email template
        template_path = os.path.join(self.base_dir, 'email.html')
        with open(template_path, 'r') as f:
            template = f.read()

        # Build the video content
        video_content = ""
        for video in processed_videos:
            summary_html = self._get_summary_html_with_groq(video)
            video_content += f"""
                <div class="video-card">
                    <p class="channel-name">Channel: {video['channel_id']}</p>
                    <a href="{video['video_url']}">
                        <img class="thumbnail" src="{video['thumbnail_url']}" alt="{video['title']}">
                    </a>
                    {summary_html}
                </div>
            """

        # Replace the placeholder with our video content and update the title
        html = template.replace('<!-- VIDEO_CONTENT_PLACEHOLDER -->', video_content)
        html = html.replace('<h1>YouTube Update</h1>', f'<h1>YouTube Update - {date_str}</h1>')
        
        return html

    def _get_summary_html_with_groq(self, video: dict) -> str:
        """
        Attempt to convert summary Markdown to HTML with OpenAI.
        Fallback to simple <p> text if that fails.
        """
        try:
            completion = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a markdown to HTML converter. Return only the raw HTML without any markdown code block formatting or HTML tags around the entire response."
                    },
                    {
                        "role": "user",
                        "content": f"Convert this markdown to HTML, returning only the raw HTML without any code block formatting:\n\n{video['summary']}"
                    }
                ],
                temperature=0.7,
                max_tokens=7000
            )
            return completion.choices[0].message.content.strip()
        except Exception as e:
            logging.warning(f"OpenAI formatting failed for {video['title']}: {str(e)}")
            fallback = video['summary'].replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br>')
            return f"<p>{fallback}</p>"

    def send_email_notification(self, subject: str, text_body: str, html_body: str) -> None:
        """
        Send email notification using Postmark API.
        """
        server_token = self.postmark_config.get('server_token')
        from_email = self.postmark_config.get('from_email')
        to_email = self.postmark_config.get('to_email')

        if not all([server_token, from_email, to_email]):
            logging.error("Email config incomplete. Skipping send.")
            return

        try:
            payload = {
                "From": from_email,
                "To": to_email,
                "Subject": subject,
                "TextBody": text_body,
                "HtmlBody": html_body,
                "MessageStream": "outbound"
            }
            response = self.http_session.post(
                "https://api.postmarkapp.com/email",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Postmark-Server-Token": server_token
                },
                json=payload,
                timeout=30
            )
            if response.status_code == 200:
                logging.info(f"Email sent successfully to {to_email}")
            else:
                logging.error(f"Failed to send email. Status: {response.status_code}, Response: {response.text}")
        except requests.RequestException as e:
            logging.error(f"Network error sending email: {str(e)}")
        except Exception as e:
            logging.error(f"Unexpected error sending email: {str(e)}")

    def process_all_channels(self, channel_ids: List[str]) -> None:
        """
        Process multiple channels in parallel. Retrieve, filter, parse, summarize,
        and then send one aggregated summary email.
        """
        all_processed_results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_channel = {
                executor.submit(self.process_channel, cid): cid for cid in channel_ids
            }
            for future in concurrent.futures.as_completed(future_to_channel):
                cid = future_to_channel[future]
                try:
                    channel_videos = future.result()
                    if channel_videos:
                        all_processed_results.extend(channel_videos)
                except Exception as e:
                    logging.error(f"Error processing channel {cid}: {e}")

        for video_data in all_processed_results:
            self.processed_videos[video_data['video_id']] = {
                'channel_id': video_data['channel_id'],
                'title': video_data['title'],
                'processed_date': video_data['processed_date'],
                'transcript_path': video_data['transcript_path'],
                'summary_path': video_data['summary_path']
            }
        self._save_processed_videos()

        if all_processed_results:
            self._send_processing_summary(all_processed_results)
        else:
            logging.info("No new videos were processed.")

if __name__ == "__main__":
    load_dotenv()

    CHANNEL_IDS = [
        "@ScenicRelaxationFilms",
        "@F1NewsTR",
        "@mkbhd",
        "@PracticalEngineeringChannel",
        "@chrisluno",
        "@mykolaharmash",
        "@backyardmayhemusa",
        "@ISHITANIFURNITURE",
        "@ChrisWillx",
        "@TheLuxuryHomeShow",
        "@kofuzi",
        "@RRBuildings",
        "@TheStudio",
        "@CleoAbram",
        "@Kenn_Ricci",
        "@MrBeast",
        "@mattp1tommy",
        "@buildshow",
        "@MauriceMoves",
        "@ColinandSamir",
        "@TomStantonEngineering",
        "@TakingCaraBabies",
        "@AutoFocus",
        "@Mitchorilla",
        "@ScottBrownCarpentry",
        "@stephenscullion262",
        "@flavourtrip",
        "@michaelrechtin",
        "@StevenBennettMakes",
        "@TomorrowsBuild",
        "@MariusHornberger",
        "@DeepDivewithAliAbdaal",
        "@DIYPerks",
        "@Strengthside",
        "@Zeltik",
        "@formulaaddict",
        "@RacingTelemetry",
        "@carlroge",
        "@AskSebby",
        "@ABIInteriors",
        "@twostraws",
        "@mattih",
        "@squarerulefurniture",
        "@casey",
        "@DesignCodeTeam",
        "@sawkasbackyard7244",
        "@PeterMcKinnon",
        "@AltShiftX"
    ]

    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    GROQ_API_KEY = os.getenv('GROQ_API_KEY')
    POSTMARK_CONFIG = {
        'server_token': os.getenv('POSTMARK_SERVER_TOKEN'),
        'from_email': os.getenv('POSTMARK_FROM_EMAIL'),
        'to_email': os.getenv('POSTMARK_TO_EMAIL')
    }

    if not all([OPENAI_API_KEY, GROQ_API_KEY, POSTMARK_CONFIG['server_token'], POSTMARK_CONFIG['from_email'], POSTMARK_CONFIG['to_email']]):
        raise ValueError("Please set all required environment variables for OpenAI/Groq/Postmark.")

    if not CHANNEL_IDS:
        raise ValueError("No channel IDs provided.")

    processor = YouTubeProcessor(
        openai_api_key=OPENAI_API_KEY,
        groq_api_key=GROQ_API_KEY,
        postmark_config=POSTMARK_CONFIG,
        max_workers=10
    )
    processor.process_all_channels(CHANNEL_IDS)