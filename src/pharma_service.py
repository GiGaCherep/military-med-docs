import requests

# База данных openFDA (США). Бесплатно, без ключа (лимит 240 запросов в минуту).
OPENFDA_URL = "https://api.fda.gov/drug/label.json"

def search_drug_info(drug_name: str) -> dict:
    """
    Ищет инструкцию к препарату в базе openFDA.
    """
    # openFDA лучше всего ищет по английским названиям. 
    # Для прототипа мы просто передаем название как есть (если оно на латинице).
    params = {
        "search": f'openfda.brand_name:"{drug_name}"+openfda.generic_name:"{drug_name}"',
        "limit": 1
    }

    try:
        response = requests.get(OPENFDA_URL, params=params, timeout=10)
        
        # Если препарат найден (код 200)
        if response.status_code == 200:
            data = response.json()
            if "results" in data and len(data["results"]) > 0:
                result = data["results"][0]
                
                # Функция для безопасного извлечения текста из списков
                def get_text(field, default="Нет данных."):
                    if field in result and isinstance(result[field], list):
                        return " ".join(result[field])
                    return default

                return {
                    "indications": get_text("indications_and_usage", "Информация о показаниях отсутствует."),
                    "contraindications": get_text("contraindications", "Информация о противопоказаниях отсутствует."),
                    "interactions": get_text("drug_interactions", "Информация о взаимодействиях отсутствует."),
                    "side_effects": get_text("adverse_reactions", "Информация о побочных эффектах отсутствует.")
                }
        
        # Если не найдено (код 404)
        return {
            "indications": f"В базе openFDA не найдено инструкции для '{drug_name}'. Убедитесь, что название указано на английском языке (например, 'Rivaroxaban' вместо 'Ривароксабан').",
            "contraindications": "Нет данных.",
            "interactions": "Нет данных.",
            "side_effects": "Нет данных."
        }

    except requests.exceptions.RequestException as e:
        return {
            "indications": f"Ошибка подключения к интернету: {e}",
            "contraindications": "Нет данных.",
            "interactions": "Нет данных.",
            "side_effects": "Нет данных."
        }

def search_disease_info(symptoms: str) -> dict:
    """
    Заглушка для поиска по симптомам. 
    (Её лучше оставить как есть для демонстрации, так как поиск диагноза по симптомам — это сложная задача для ИИ).
    """
    symptoms_lower = symptoms.lower()
    if "температур" in symptoms_lower or "кашел" in symptoms_lower or "орви" in symptoms_lower:
        return {
            "diagnosis": "ОРВИ (Острая респираторная вирусная инфекция)",
            "medications": [{"name": "Paracetamol", "dosage": "500 мг 3 раза в день", "days": 5}]
        }
    elif "ране" in symptoms_lower or "ранени" in symptoms_lower:
        return {
            "diagnosis": "Рваная рана мягких тканей",
            "medications": [{"name": "Amoxicillin", "dosage": "500 мг 3 раза в день", "days": 7}]
        }
    return {"diagnosis": "Не удалось определить", "medications": []}