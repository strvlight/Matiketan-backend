import requests

# ← I-change kung physical phone ang gamiton
BASE_URL = 'http://127.0.0.1:8000/api'

def simulate_detection(plate, location, confidence):
    print(f'\n[SIMULATING] Sending detection...')
    print(f'  Plate: {plate}')
    print(f'  Location: {location}')
    print(f'  Confidence: {confidence}%')

    try:
        response = requests.post(
            f'{BASE_URL}/detections/receive/',
            json={
                'plate_number': plate,
                'location': location,
                'confidence_score': confidence,
                'violation_type': 'Illegal Parking',
            }
        )

        if response.status_code == 201:
            data = response.json()
            print(f'  ✅ Detection sent! ID: {data["id"]}')
        else:
            print(f'  ❌ Failed: {response.status_code}')
            print(f'  Response: {response.text}')

    except Exception as e:
        print(f'  ❌ Error: {e}')


# ← I-change ang plate + location para sa demo
simulate_detection(
    plate='ABC 1234',
    location='J.C. Aquino Ave., Butuan City',
    confidence=94.5
)