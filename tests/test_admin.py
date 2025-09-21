import os
from fastapi.testclient import TestClient
from app.main import app

class DummyMethod:
    def __init__(self, message_count=0, consumer_count=0):
        self.message_count = message_count
        self.consumer_count = consumer_count

class DummyQueue:
    def __init__(self, message_count=0, consumer_count=0):
        self.method = DummyMethod(message_count, consumer_count)

class DummyChannel:
    def __init__(self):
        self.declared = False
    def queue_declare(self, queue, durable=True, passive=False):
        self.declared = True
        return DummyQueue(5, 1)

class DummyConn:
    def __init__(self):
        self._channel = DummyChannel()
    def channel(self):
        return self._channel
    def close(self):
        pass


def test_admin_queue_and_drain(monkeypatch):
    os.environ['RABBITMQ_URL'] = 'amqp://guest:guest@localhost:5672/%2F'

    import app.main as mainmod

    def fake_blocking_conn(params):
        return DummyConn()

    monkeypatch.setattr(mainmod.pika, 'BlockingConnection', fake_blocking_conn)

    client = TestClient(app)

    # Check queue stats endpoint
    r = client.get('/admin/queue')
    assert r.status_code == 200
    data = r.json()
    assert data['enabled'] is True
    assert data['queue']
    assert data['message_count'] == 5

    # Drain endpoint should return ok
    r2 = client.post('/admin/drain')
    assert r2.status_code == 200
    assert r2.json()['status'] == 'ok'
