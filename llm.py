import os
import openai
openai.api_key = os.getenv("OPENAI_API_KEY")

from config import DEFAULT_MODEL_VERSION
def ask_model_openai(system_prompt, user_prompt): 
    response = openai.chat.completions.create( 
        model=DEFAULT_MODEL_VERSION, 
        messages=[ 
            {"role": "system", "content": system_prompt}, 
            {"role": "user", "content": user_prompt} 
        ], 
        temperature=0.5 
    ) 
    return response.choices[0].message.content.strip()