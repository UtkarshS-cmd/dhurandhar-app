import uuid
import httpx

BASE = 'http://127.0.0.1:5000/api'

def test_health_and_catalog():
    with httpx.Client(base_url=BASE, timeout=10) as c:
        assert c.get('/health').status_code == 200
        cities = c.get('/cities').json()
        assert len(cities) >= 10
        city = next(x for x in cities if x['name'] == 'Mumbai')
        theaters = c.get('/theaters', params={'city_id': city['id']}).json()
        assert theaters
        shows = c.get('/shows', params={'theater_id': theaters[0]['id'], 'date': __import__('datetime').date.today().isoformat()}).json()
        assert shows
        seats = c.get(f"/shows/{shows[0]['id']}/seats").json()
        assert len(seats) == 80


def test_booking_price_and_double_booking():
    with httpx.Client(base_url=BASE, timeout=10) as c:
        city = next(x for x in c.get('/cities').json() if x['name'] == 'Mumbai')
        theater = c.get('/theaters', params={'city_id': city['id']}).json()[0]
        date = __import__('datetime').date.today().isoformat()
        show = c.get('/shows', params={'theater_id': theater['id'], 'date': date}).json()[0]
        seats = c.get(f"/shows/{show['id']}/seats").json()
        selected = [x for x in seats if x['status'] == 'AVAILABLE' and x['category'] == 'Silver'][:2]
        assert len(selected) == 2
        user = {'full_name':'Pytest User','email':f'test-{uuid.uuid4().hex[:8]}@example.com','phone':'9876543210','password':'Test@1234'}
        hold = c.post('/bookings/hold', json={'user':user,'show_id':show['id'],'seat_ids':[x['id'] for x in selected],'payment_method':'PENDING'})
        assert hold.status_code == 201
        data = hold.json()
        assert float(data['total_amount']) == 700
        conflict = c.post('/bookings/hold', json={'user':{**user,'email':f'other-{uuid.uuid4().hex[:8]}@example.com'},'show_id':show['id'],'seat_ids':[x['id'] for x in selected],'payment_method':'PENDING'})
        assert conflict.status_code == 409
        confirmed = c.post(f"/bookings/{data['booking_reference']}/confirm", json={'payment_method':'UPI / Google Pay / PhonePe'})
        assert confirmed.status_code == 200
        assert confirmed.json()['status'] == 'CONFIRMED'
        assert confirmed.json()['payment_status'] == 'PAID'
        conflict2 = c.post('/bookings/hold', json={'user':{**user,'email':f'other2-{uuid.uuid4().hex[:8]}@example.com'},'show_id':show['id'],'seat_ids':[x['id'] for x in selected],'payment_method':'PENDING'})
        assert conflict2.status_code == 409


def test_invalid_seat_and_auth():
    with httpx.Client(base_url=BASE, timeout=10) as c:
        city = next(x for x in c.get('/cities').json() if x['name'] == 'Delhi')
        theater = c.get('/theaters', params={'city_id': city['id']}).json()[0]
        date = __import__('datetime').date.today().isoformat()
        show = c.get('/shows', params={'theater_id': theater['id'], 'date': date}).json()[0]
        user = {'full_name':'Auth Test','email':f'auth-{uuid.uuid4().hex[:8]}@example.com','phone':'9876543210','password':'Test@1234'}
        reg = c.post('/auth/register', json=user)
        assert reg.status_code == 201
        login = c.post('/auth/login', json={'email':user['email'],'password':user['password']})
        assert login.status_code == 200
        bad = c.post('/auth/login', json={'email':user['email'],'password':'wrong'})
        assert bad.status_code == 401
        invalid = c.post('/bookings/hold', json={'user':{**user,'email':f'invalid-{uuid.uuid4().hex[:8]}@example.com'},'show_id':show['id'],'seat_ids':[999999],'payment_method':'PENDING'})
        assert invalid.status_code == 422
