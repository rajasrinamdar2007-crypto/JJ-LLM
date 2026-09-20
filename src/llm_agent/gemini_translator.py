from google import genai
from google.genai import types

class RuleTranslator:
    def __init__(self):
        # This turns on the Gemini brain
        self.client = genai.Client()
        
        # These are the strict rules we force Gemini to follow so it doesn't break The Watcher
        self.system_prompt = """
        You are a translator for a road-damage detection system.
        You MUST output ONLY valid YAML matching schema_version: 2.
        Do not include markdown blocks like ```yaml. Do not say 'Here is your code'.
        Constraints:
        - rule_id must match the provided filename exactly.
        - Steps must only contain 'check' and 'on_exit'.
        - Valid ops are: is_visible, confidence, persisted_for, avg_in_window, bbox.
        """

    def translate_to_yaml(self, english_sentence: str, rule_name: str) -> str:
        prompt = f"Make a rule for this: '{english_sentence}'. The rule_id is: {rule_name}"
        
        print("Asking Gemini to translate...")
        
        # We use temperature=0 so Gemini doesn't get creative. We want strict, boring YAML.
        response = self.client.models.generate_content(
               model="gemini-3.6-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                temperature=0.0 
            )
        )
        
        return response.text.strip()

# Let's test it to see if it works!
if __name__ == "__main__":
    translator = RuleTranslator()
    
    # We pretend you typed this into the app
    my_english = "Fire an event when a pothole is tracked for 5 frames."
    my_rule_name = "pothole_persist_5"
    
    result = translator.translate_to_yaml(my_english, my_rule_name)
    
    print("\n--- GEMINI WROTE THIS YAML ---")
    print(result)