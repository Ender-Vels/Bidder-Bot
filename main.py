from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import requests
import os
import threading
import time
from requests_oauthlib import OAuth2Session
import secrets

app = Flask(__name__)

# Генерация секретного ключа, если он не установлен
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(16))

# OAuth2 Configuration
client_id = '08555429-03a8-4924-8336-6a302feeaedd'
client_secret = 'eb5a159fe8af4923799e32c8b3acab4a9de432f7cc300b19c1352943dc84ca21d49e6fbbde6e33186c5dd672ccc24d20607a606921734f7b87cd5ab19442a57d'
authorization_base_url = 'https://accounts.freelancer.com/oauth/authorize'
token_url = 'https://accounts.freelancer.com/oauth/token'
redirect_uri = 'https://bidder-bot.vercel.app/callback'

scope = [
    'basic',
    'fln:project_create',
    'fln:project_manage',
    'fln:contest_create',
    'fln:contest_manage',
    'fln:messaging',
    'fln:user_information'
]

is_running = False

@app.route('/')
def home():
    if 'oauth_token' not in session:
        return redirect(url_for('login'))

    try:
        filters_data_skill = fetch_data("https://www.freelancer.com/api/projects/0.1/jobs/")
        filters_data_countries = fetch_data("https://www.freelancer.com/api/common/0.1/countries")['countries']
        filters_data_currencies = fetch_data("https://www.freelancer.com/api/projects/0.1/currencies/")['currencies']
    except Exception as e:
        return f"Failed to retrieve data: {e}"

    return render_template('index.html', skills=filters_data_skill, countries=filters_data_countries, currencies=filters_data_currencies)

@app.route('/login')
def login():
    freelancer = OAuth2Session(client_id, scope=scope, redirect_uri=redirect_uri)
    authorization_url, state = freelancer.authorization_url(authorization_base_url)
    session['oauth_state'] = state
    return redirect(authorization_url)

@app.route('/callback')
def callback():
    freelancer = OAuth2Session(client_id, state=session.get('oauth_state'), redirect_uri=redirect_uri)
    try:
        token = freelancer.fetch_token(token_url, client_secret=client_secret, authorization_response=request.url)
    except Exception as e:
        return f"Failed to fetch token: {e}"
    session['oauth_token'] = token
    return redirect(url_for('home'))

@app.route('/submit', methods=['POST'])
def submit():
    global is_running
    is_running = True

    if 'oauth_token' not in session:
        return redirect(url_for('login'))

    freelancer_api = request.form.get('freelancer-api')
    skills = request.form.getlist('skills')
    skill_ids = request.form.getlist('skill_ids')
    countries = request.form.getlist('countries')
    currencies = request.form.getlist('currencies')
    min_rate = request.form.get('min-rate')
    max_rate = request.form.get('max-rate')
    fixed_rate = request.form.get('fixed-rate')
    gpt_token = request.form.get('gpt-token')
    example_message = request.form.get('example-message')

    print(f"Freelancer API URL: {freelancer_api}")
    print(f"Selected Skills: {skills}")
    print(f"Skill IDs: {skill_ids}")
    print(f"Selected Countries: {countries}")
    print(f"Selected Currencies: {currencies}")
    print(f"Minimum Rate per Hour: {min_rate}")
    print(f"Maximum Rate per Hour: {max_rate}")
    print(f"Fixed Rate: {fixed_rate}")
    print(f"GPT Token: {gpt_token}")
    print(f"Example Message: {example_message}")

    def fetch_projects():
        while is_running:
            try:
                oauth_token = session['oauth_token']
                freelancer = OAuth2Session(client_id, token=oauth_token)
                headers = {'Authorization': f'Bearer {oauth_token["access_token"]}'}
                params = {
                    'project_types[]': ['fixed', 'hourly'],
                    'min_price': min_rate,
                    'max_price': max_rate,
                    'countries[]': countries,
                    'currencies[]': currencies,
                    'sort_field': 'time_updated',
                    'limit': 1000,
                    'offset': 0,
                }
                response = freelancer.get('https://www.freelancer.com/api/projects/0.1/projects/active/', headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                projects = data.get('result', {}).get('projects', [])
                filtered_projects = [project for project in projects if any(skill in project.get('seo_url', '') for skill in skills)]
                print(f'Found projects: {len(filtered_projects)}')
                for project in filtered_projects:
                    create_bid(project)
            except Exception as e:
                print(f'Error fetching projects: {e}')
            time.sleep(60)

    def generate_alternatives():
        try:
            response = requests.post('https://api.openai.com/v1/chat/completions', headers={'Authorization': f'Bearer {gpt_token}', 'Content-Type': 'application/json'}, json={'model': 'gpt-3.5-turbo', 'messages': [{'role': 'user', 'content': example_message}], 'n': 1})
            response.raise_for_status()
            response_data = response.json()
            alternatives = [choice['message']['content'] for choice in response_data['choices']]
            print('Generated alternatives:')
            for alternative in alternatives:
                print(alternative)
            return alternatives
        except Exception as e:
            print(f'Error generating alternatives: {e}')
            return []

    def create_bid(project):
        try:
            project_id = project.get('id')
            project_currency = project.get('currency', {}).get('code', 'USD')
            project_type = project.get('type', 'fixed')
            description = generate_alternatives()[0]

            data = {
                'project_id': project_id,
                'bid': {
                    'amount': max_rate,
                    'description': 'просто напиши альтернативный вариант этого сообщения и ничего более\n' + description,
                    'duration': 5,
                    'currency': project_currency,
                    'type': project_type,
                    'skills': skill_ids,
                }
            }

            api_token = freelancer_api
            headers = {
                'freelancer-oauth-v1': api_token,
                'Authorization': f'Bearer {api_token}',
                'Content-Type': 'application/json'
            }

            response = requests.post('https://www.freelancer.com/api/projects/0.1/bids/', headers=headers, json=data)
            response.raise_for_status()
            result = response.json()
            print(f'Bid created successfully for project {project_id}: {result}')
        except Exception as e:
            print(f'Error creating bid for project {project_id}: {e}')

    threading.Thread(target=fetch_projects, daemon=True).start()

    return jsonify({'message': 'Success'}), 200

@app.route('/stop', methods=['POST'])
def stop():
    global is_running
    is_running = False
    return jsonify({'message': 'Stopped'}), 200

@app.route('/logout')
def logout():
    session.pop('oauth_token', None)
    return redirect(url_for('home'))

def fetch_data(url):
    response = requests.get(url)
    response.raise_for_status()
    return response.json()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
