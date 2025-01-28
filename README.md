# YouTube Newsletter Generator

An automated system that generates personalized email newsletters from YouTube content using AI-powered transcription and summarization.

## 🚀 Features

- **Automated Video Processing**: Fetches and processes new videos from specified YouTube channels
- **AI-Powered Analysis**: 
  - Smart transcription generation
  - Context-aware summarization
  - Key points extraction
- **Advanced Email System**:
  - Responsive design with dark mode
  - Mobile-optimized layout
  - Rich media support
- **Performance Optimized**:
  - Multi-threaded processing
  - Efficient caching system
  - Rate limiting protection
- **Multiple AI Providers**:
  - OpenAI integration
  - Groq support
  - Extensible provider system

## 📋 Prerequisites

- Python 3.9+
- Active API keys for:
  - OpenAI
  - Groq
  - YouTube Data API v3
- Postmark account for email delivery
- Unix-like operating system for cron support

## 🛠 Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/youtube-newsletter-generator.git
cd youtube-newsletter-generator
```

2. Set up virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your API keys and settings
```

## ⚙️ Configuration

Create a `.env` file with the following variables:

```env
OPENAI_API_KEY=your_key_here
GROQ_API_KEY=your_key_here
POSTMARK_SERVER_TOKEN=your_token_here
POSTMARK_FROM_EMAIL=sender@domain.com
POSTMARK_TO_EMAIL=recipient@domain.com
YOUTUBE_API_KEY=your_youtube_key
```

## 📝 Usage

### Manual Execution

```bash
python youtube_processor.py
```

### Automated Execution

Add to crontab to run daily at 11:00 AM:
```bash
0 11 * * * /path/to/run_youtube_processor.sh
```

## 📁 Project Structure

```
.
├── src/
│   ├── processors/
│   │   ├── video_processor.py
│   │   ├── transcript_generator.py
│   │   └── summarizer.py
│   ├── email/
│   │   ├── templates/
│   │   └── sender.py
│   └── utils/
├── tests/
├── templates/
│   └── email.html
├── config/
├── requirements.txt
└── README.md
```

## 🔄 Workflow

1. Fetch new videos from configured channels
2. Generate transcripts using AI
3. Create summaries and extract key points
4. Format content into email template
5. Send personalized newsletter

## 🤝 Contributing

Contributions welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

MIT License - see [LICENSE](LICENSE) for details

## 🙏 Acknowledgments

- [OpenAI](https://openai.com) - AI processing
- [Groq](https://groq.com) - AI acceleration
- [Postmark](https://postmarkapp.com) - Email delivery
- [YouTube Data API](https://developers.google.com/youtube/v3) - Video data access