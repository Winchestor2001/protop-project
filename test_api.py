import requests

def test_admin_api():
    session = requests.Session()
    login_url = 'http://127.0.0.1:5002/admin/login'
    data = {'username': 'adminJ', 'password': 'Mavlonovzava2010'}
    
    # Perform login
    res = session.post(login_url, data=data)
    if res.status_code != 200 and 'admin' not in res.url:
        print("Login failed, status:", res.status_code)
        return
    
    print("Login successful.")
    
    # Hit the API
    api_url = 'http://127.0.0.1:5002/api/admin/specialists/list'
    res = session.get(api_url)
    if res.status_code != 200:
        print("API failed, status:", res.status_code)
        return
        
    try:
        data = res.json()
        print("API Response keys:", list(data.keys()))
        if 'specialists' in data:
            print("Number of specialists:", len(data['specialists']))
            if len(data['specialists']) > 0:
                print("First specialist:", data['specialists'][0])
        else:
            print("No 'specialists' key in response.")
    except Exception as e:
        print("Failed to decode JSON:", e)

if __name__ == '__main__':
    test_admin_api()
