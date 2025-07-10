import time
from typing import Type, Union, Dict, Any
from pydantic import BaseModel
import dirtyjson
import re
# Make sure you install dirtyjson: pip install dirtyjson

# === Optionally, import your Groq client from where you configure it ===

# === Helper function ===

def call_llm_and_parse(
    groq_client,
    prompt: str,
    model: Type[BaseModel],
    max_retries: int = 3,
    delay: float = 1.0
) -> Union[BaseModel, Dict[str, Any]]:
    """
    Call LLM with a prompt, parse the JSON response, and validate it using a Pydantic model.
    
    Args:
        prompt (str): The prompt to send to the LLM.
        model (Type[BaseModel]): The Pydantic model to validate against.
        max_retries (int, optional): Number of retries on failure. Default is 3.
        delay (float, optional): Delay (in seconds) between retries, multiplied by attempt count.
    
    Returns:
        BaseModel: Validated Pydantic model instance if successful.
        dict: Contains 'error' and 'raw' fields if validation fails after retries.
    """
    for attempt in range(1, max_retries + 1):
        try:
            print(f"[call_llm_and_parse] Attempt {attempt}: sending prompt to LLM...")

            completion = groq_client.chat.completions.create(
                model="llama3-8b-8192",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=800
            )

            response_text = completion.choices[0].message.content
            print(f"[call_llm_and_parse] Raw LLM response: {response_text[:200]}...")  # first 200 chars

            # Extract JSON (handle dirty or partial JSON)
            json_str = extract_and_repair_json(response_text)

            # Parse JSON using dirtyjson
            parsed = dirtyjson.loads(json_str)

            # Validate with Pydantic
            validated = model.model_validate(parsed)

            print("[call_llm_and_parse] Successfully parsed and validated.")
            return validated

        except Exception as e:
            print(f"[Retry {attempt}] Error: {e}")
            if attempt < max_retries:
                time.sleep(delay * attempt)
            else:
                print("[call_llm_and_parse] Failed after retries.")
                return {
                    "error": f"Validation failed after {max_retries} retries: {e}",
                    "raw": json_str if 'json_str' in locals() else response_text
                }


def extract_and_repair_json(text: str) -> str:
    """
    Extracts JSON starting from first '{' and balances braces.
    """
    match = re.search(r'\{[\s\S]*', text)
    if not match:
        raise ValueError("No JSON object found.")
    json_str = match.group()
    # Fix unmatched braces
    open_braces = json_str.count('{')
    close_braces = json_str.count('}')
    if open_braces > close_braces:
        json_str += '}' * (open_braces - close_braces)
    return json_str