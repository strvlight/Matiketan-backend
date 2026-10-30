from django.test import TestCase
from django.test import override_settings
from rest_framework.test import APITestCase
@override_settings(DETECTOR_API_KEY='testkey')
class DetectionTests(APITestCase):
    def test_receive_requires_key(self):
        r = self.client.post('/api/detections/receive/', {'plate_number': 'ABC123'})
        self.assertEqual(r.status_code, 401)

    def test_receive_missing_plate(self):
        r = self.client.post('/api/detections/receive/', {},
                             HTTP_X_DETECTOR_KEY='testkey')
        self.assertEqual(r.status_code, 400)

    def test_receive_ok_without_image(self):
        r = self.client.post('/api/detections/receive/',
                             {'plate_number': 'abc123', 'location': 'Test',
                              'confidence_score': 90},
                             HTTP_X_DETECTOR_KEY='testkey')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['plate_number'], 'ABC123')
        self.assertFalse(r.data['has_image'])