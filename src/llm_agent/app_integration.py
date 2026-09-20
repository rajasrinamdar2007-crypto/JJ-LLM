import os

# We bring in the two friends we created in Step 1 and Step 2
from .gemini_translator import RuleTranslator
from .rule_validator import RuleValidator, RuleSyntaxError
def create_and_save_rule(user_english: str, rule_name: str):
    translator = RuleTranslator()
    validator = RuleValidator()
    
    print(f"Asking Gemini to translate: '{user_english}'...")
    
    # 1. Ask Gemini to write the YAML code
    yaml_code = translator.translate_to_yaml(user_english, rule_name)
    
    # 2. Force the Validator to check the code before we save it
    try:
        validator.parse_rule(yaml_code, rule_name)
        print("Validator says: The code looks perfect!")
        
        # 3. Create the 'rules' folder if it doesn't exist yet
        os.makedirs("rules", exist_ok=True)
        
        # 4. Save the open code as a real .yaml file
        file_path = f"rules/{rule_name}.yaml"
        with open(file_path, "w") as file:
            file.write(yaml_code)
            
        print(f"Success! The rule is saved and ready at: {file_path}")
        return True
        
    except RuleSyntaxError as error:
        # If Gemini made a mistake, we catch it here and DO NOT save the file
        print(f"Uh oh, the Validator caught a mistake: {error}")
        return False

# Let's test the whole pipeline!
if __name__ == "__main__":
    my_intent = "Fire an event when a pothole is tracked for 5 frames."
    my_rule_id = "pothole_persist_5"
    
    create_and_save_rule(my_intent, my_rule_id)