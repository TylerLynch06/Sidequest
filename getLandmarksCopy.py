##run venv with source .venv/bin/activate
import os
import pathlib
from dotenv import load_dotenv
from openai import OpenAI

# Get the current directory and load the .env file
current_dir = pathlib.Path(__file__).parent.absolute()
env_path = current_dir / '.env'
load_dotenv(dotenv_path=env_path)

# Get API key from environment
api_key = os.environ.get("OPENROUTER_API_KEY")

# Check if API key is available
if not api_key:
    raise ValueError("OpenRouter API key not found. Please set OPENROUTER_API_KEY in your .env file.")

# Create a client for OpenRouter
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
    #default_headers={"HTTP-Referer": "http://localhost:5000"}  # Required by OpenRouter
)

response = client.chat.completions.create(
    model="qwen/qwen3.8-27b:nitro",
    max_tokens=400,
    temperature=0.3,
    extra_body={"reasoning": {"enabled": False}},   # turn off thinking, if the model supports it
    messages=[
        {"role": "system", "content": (
            "You suggest points of interest near a route. Rules: each POI is at most 5 miles "
            "from the route; skip obvious places on the route itself. Reply with ONLY a PRETTY JSON "
            'array, no other text: [{"name": "...", "lon": 0.0, "lat": 0.0}]. Max 8 items.'
        )},
        {"role": "user", "content": "Route: St Andrews to Dundee"},
    ],
)

# Display the response
print(response.choices[0].message.content)
with open("routeData.json", "w") as f:
  f.write(response.choices[0].message.content)