import os, json, pathlib
from dotenv import load_dotenv
from openai import OpenAI
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

load_dotenv(pathlib.Path(__file__).parent / ".env")
client = OpenAI(base_url="https://openrouter.ai/api/v1",
                api_key=os.environ["OPENROUTER_API_KEY"])

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"],  # fine for a hackathon, tighten later
                   allow_methods=["*"], allow_headers=["*"])

cache = {}  # (start, end) -> list of POIs, so repeat requests are instant

@app.get("/api/pois")
def pois(start: str, end: str):
    key = (start.lower(), end.lower())
    if key in cache:
        return cache[key]

    r = client.chat.completions.create(
        model="qwen/qwen3.8-27b:nitro",
        max_tokens=400,
        temperature=0.3,
        extra_body={"reasoning": {"enabled": False}},
        messages=[
            {"role": "system", "content": (
                "You suggest points of interest near a route. Each POI is at most 5 miles "
                "from the route; skip obvious places on the route itself. Reply with ONLY a "
                'JSON array, no other text: [{"name": "...", "lon": 0.0, "lat": 0.0}]. Max 8 items.')},
            {"role": "user", "content": f"Route: {start} to {end}"},
        ],
    )
    text = r.choices[0].message.content.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise HTTPException(502, "Model did not return valid JSON")
    cache[key] = data
    return data

@app.get("/api/poi-info")
def poi_info(poiName: str):
    key = ("info", poiName.lower())
    if key in cache:
        return cache[key]

    r = client.chat.completions.create(
        model="qwen/qwen3.8-27b:nitro",
        max_tokens=150,
        temperature=0.3,
        extra_body={"reasoning": {"enabled": False}},
        messages=[
            {"role": "system", "content": "Give a summary of about 50 words about a location, plain text only."},
            {"role": "user", "content": f"Location: {poiName}"},
        ],
    )
    summary = r.choices[0].message.content.strip()
    data = {"name": poiName, "summary": summary}
    cache[key] = data
    return data