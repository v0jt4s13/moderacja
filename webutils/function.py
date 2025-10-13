from flask import request
from datetime import datetime
import json

def extract_date(prefix, default):
    return {
        "day": request.args.get(f"{prefix}_day", f"{default.day:02}"),
        "month": request.args.get(f"{prefix}_month", f"{default.month:02}"),
        "year": request.args.get(f"{prefix}_year", f"{default.year}")
}

def dict_to_datetime(date_dict):
    return datetime(
        int(date_dict["year"]),
        int(date_dict["month"]),
        int(date_dict["day"])
)

def analyze_file(log_path):
    data = None
    file_type = None
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            try:
                print(f'[A] Sprawdzamy czy plik "{log_path}" ma dane formatu JSON/Dict.')
                data = json.load(f)
                file_type = "json"
                print(f'[A] Plik "{log_path}" został rozpoznany jako JSON/Dict.')
                print(f'[A] Typ danych: {type(data)}, Długość: {len(data) if isinstance(data, list) or isinstance(data, dict) else 0}')
                if isinstance(data, list) and len(data) > 10:
                    print(f'[A] Pierwsze 10 elementów: {data[:10]}')
                elif isinstance(data, dict):
                    print(f'[A] Pierwsze kilka kluczy: {list(data.keys())[:10]}')

            except json.JSONDecodeError:
                print(f'[B] Plik "{log_path}" nie jest poprawnym formatem JSON.')
                f.seek(0)  # Powrót na początek pliku po nieudanej próbie JSON
                
                try:
                    print(f'[B] Próba odczytu jako log (linia po linii).')
                    data = f.read().splitlines()
                    file_type = "log"
                    print(f'[B] Plik "{log_path}" został rozpoznany jako log (linia po linii).')
                    print(f'[B] Typ danych: {type(data)}; data[0] type: {type(data[0])}; Liczba linii: {len(data)}')
                    if len(data) > 3:
                        print(f"[B] Pierwsze 3 linie: - wyłączone wyświetlanie") #\n{data[0]}\n{data[1]}\n{data[2]}")
                        # print(f"\n{data[0]}") #\n{data[1]}\n{data[2]}")
            
                except Exception as e:
                    for line in f:
                        # Parse each line as JSON
                        try:
                            data = json.loads(line)
                            file_type = "jsonl"
                            print(f'[C] Plik "{log_path}" został rozpoznany jako JSONL/DictL.')
                            print(f'[C] Typ danych: {type(data[0])}/{file_type}, Keys: {data[0].keys()}')
                            break
                            # if len(data) > 10:
                            #     print(f'[B] Pierwsze 10 linii: {data[:10]}')
                        
                        except json.JSONDecodeError as e:
                            print(f"Error parsing line: {line}")
                            print(e)
                    
                
                # try:
                #     data = json.load(data[0])
                    
                # except json.JSONDecodeError:

                #     file_type = "log"
                #     print(f'[C] Plik "{log_path}" został rozpoznany jako log (linia po linii).')
                #     print(f'[C] Typ danych: {type(data)}, Liczba linii: {len(data)}')
                #     if len(data) > 10:
                #         print(f'[C] Pierwsze 10 linii: {data[:10]}')
            
            except Exception as e:
                print(f'Wystąpił nieoczekiwany błąd podczas odczytu pliku: {e}')
    except FileNotFoundError:
        print(f'Plik "{log_path}" nie został znaleziony.')
    except Exception as e:
        print(f'Wystąpił ogólny błąd: {e}')

    return {"file_type": file_type, "data": data}

