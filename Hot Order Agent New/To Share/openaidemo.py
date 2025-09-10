import os,openai
from dotenv import load_dotenv

load_dotenv(override=True)
openai_api_key = os.getenv('OPENAI_API_KEY')
openai_base_url = os.getenv('OPENAI_BASE_URL')
client = openai.OpenAI(api_key=openai_api_key, base_url=openai_base_url)
#client = openai.OpenAI(api_key=openai_api_key, base_url=openai_base_url)

chat_completion = client.chat.completions.create(
    messages=[{
            "role": "user",
            "content": "How many toes do dogs have?",
    }],
    model="gpt-5-nano-2025-08-07",
)

print(chat_completion.choices[0].message.content)