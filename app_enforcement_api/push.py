import json
import os

from .models import DeviceToken


def _init():
    import firebase_admin
    from firebase_admin import credentials
    if firebase_admin._apps:
        return True
    raw = os.getenv('FIREBASE_CREDENTIALS_JSON', '')
    if not raw:
        return False
    firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
    return True


def notify_detection(detection):
    try:
        if not _init():
            print('[PUSH] FIREBASE_CREDENTIALS_JSON not set, skipping')
            return
        from firebase_admin import messaging
        tokens = list(DeviceToken.objects.values_list('token', flat=True))
        if not tokens:
            return
        msg = messaging.MulticastMessage(
            notification=messaging.Notification(
                title='Illegal Parking Detected',
                body=f'Plate {detection.plate_number} at {detection.location}',
            ),
            data={'detection_id': str(detection.id)},
            tokens=tokens,
        )
        resp = messaging.send_each_for_multicast(msg)
        print(f'[PUSH] sent={resp.success_count} failed={resp.failure_count}')
        for tok, r in zip(tokens, resp.responses):
            if not r.success:
                DeviceToken.objects.filter(token=tok).delete()
    except Exception as e:
        print(f'[PUSH] error: {e}')