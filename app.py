"""실행 파일: python app.py → Gradio 앱 시작."""
from chatbot.ui.gradio_app import ChatApp

if __name__ == "__main__":
    ChatApp().build_ui().launch()
