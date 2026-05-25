import csv
import hashlib
import os
import re
import time
from datetime import datetime
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# configuration : files

DATA_DIR = "data"
PUBLIC_USERS_FILE = os.path.join(DATA_DIR, "users_public.csv")
PRIVATE_USERS_FILE = os.path.join(DATA_DIR, "users_private.csv")
TASKS_FILE = os.path.join(DATA_DIR, "tasks.csv")
LISTS_FILE = os.path.join(DATA_DIR, "lists.csv")
SUBMISSIONS_FILE = os.path.join(DATA_DIR, "submissions.csv")

# configuration : links

BASE_URL = "https://themis.ii.uni.wroc.pl"
CONTEST_URL = f"{BASE_URL}/SP3_2526_7"
DEFAULT_DATE = "2000-01-01 00:00:00"

# configuration : login

load_dotenv()
USER = os.getenv("THEMIS_USER")
PASSWORD = os.getenv("THEMIS_PASSWORD")

# utils

def ensure_data_dir():

    """Ensures that the data directory exists."""

    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

def generate_hash(text):

    """Creates a short, unique hash for a username."""

    return hashlib.sha256(text.encode()).hexdigest()[:12]

def read_csv_safely(file_path, columns):

    """Reads a CSV file or returns an empty DataFrame with specified columns if it doesn't exist or is empty."""
    
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return pd.read_csv(file_path, sep=';', encoding='utf-8-sig')
    
    return pd.DataFrame(columns=columns)

def save_csv_safely(df, file_path):

    """Saves a DataFrame to CSV with consistent settings."""

    ensure_data_dir()
    df.to_csv(file_path, index=False, sep=';', encoding='utf-8-sig')


# main functions

def get_session():

    """Initializes a session and logs into Themis."""

    session = requests.Session()

    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })

    session.get(f"{BASE_URL}/")
    login_url = f"{BASE_URL}/login"
    payload = {'userid': USER, 'passwd': PASSWORD}

    response = session.post(login_url, data=payload, headers={'Referer': f"{BASE_URL}/"})
    print(f"🔑 Login Status: {response.status_code}")
    
    if "You're not logged in" not in response.text:
        print("✅ Success! Logged in.")
        return session
    else:
        print("❌ Login failed.")
        return None


def sync_students_from_ranking(session):

    """Fetches students from the ranking page and registers new ones."""

    print("🏆 Fetching student list from ranking...")

    ensure_data_dir()

    response = session.get(f"{CONTEST_URL}/ranks")
    soup = BeautifulSoup(response.text, 'html.parser')
    users_container = soup.find('div', id='ranks-users')
    
    if not users_container:
        print("❌ Could not find 'ranks-users' div.")
        return {}

    found_students = {}
    for link in users_container.find_all('a', href=True):
        if '/status/' in link['href']:
            login = link['href'].split('/')[-1].strip(',')
            nickname = link.get_text(strip=True)
            found_students[login] = nickname

    print(f"👥 Found {len(found_students)} students in ranking.")

    df_private = read_csv_safely(PRIVATE_USERS_FILE, ['hash', 'username', 'nickname', 'full_name'])
    df_public = read_csv_safely(PUBLIC_USERS_FILE, ['hash', 'last_sync_date'])

    new_public_rows = []
    new_private_rows = []

    for login, nick in found_students.items():
        if login not in df_private['username'].values:
            user_hash = generate_hash(login)
            
            new_public_rows.append({
                'hash': user_hash,
                'last_sync_date': DEFAULT_DATE
            })
            new_private_rows.append({
                'hash': user_hash,
                'username': login,
                'nickname': nick,
                'full_name': ''
            })

    if new_public_rows:
        df_public_final = pd.concat([df_public, pd.DataFrame(new_public_rows)], ignore_index=True)
        df_private_final = pd.concat([df_private, pd.DataFrame(new_private_rows)], ignore_index=True)
        
        save_csv_safely(df_public_final, PUBLIC_USERS_FILE)
        save_csv_safely(df_private_final, PRIVATE_USERS_FILE)
        print(f"✅ Registered {len(new_public_rows)} new users.")
    else:
        print("ℹ️ No new students to add. Keeping existing files.")

    df_priv_current = read_csv_safely(PRIVATE_USERS_FILE, ['hash', 'username'])
    return dict(zip(df_priv_current['username'], df_priv_current['hash']))


def get_lists(session):

    """Checks for tabs (lists) on the main contest page and stores them."""

    print("📋 Checking contest lists...")
    df_old = read_csv_safely(LISTS_FILE, ['id','nr', 'subject', 'type'])
    existing_ids = df_old['id'].tolist()

    response = session.get(CONTEST_URL)
    soup = BeautifulSoup(response.text, 'html.parser')
    all_tabs = soup.find_all('a', class_='secind-tab')

    new_lists = []
    current_subject = ""
    special_types = ["uzupełniające", "uzupełniająca", "dodatkowe", "zadanie ukryte", "ukryte"]

    for tab in all_tabs:
        full_name = tab.get_text(separator=" ", strip=True)
        url = tab.get('href')

        if " - " not in full_name or "Lista" not in full_name:
            continue

        clean_parts = full_name[full_name.find("Lista"):].split("-", 1)
        lista_nr = clean_parts[0].replace("Lista", "").strip()
        raw_desc = clean_parts[1].strip() if len(clean_parts) > 1 else ""

        if any(st in raw_desc.lower() for st in special_types):
            list_type = "uzupełniające" if raw_desc == "uzupełniająca" else raw_desc
        else:
            current_subject = raw_desc
            list_type = "podstawowa"

        id = url.split('#')[-1]

        if id not in existing_ids:
            print(f"✨ Found new list: {lista_nr} | {current_subject} ({list_type})")

            new_lists.append({
                'id': id,
                'nr': lista_nr,
                'subject': current_subject,
                'type': list_type,
            })

    if new_lists:
        df_final = pd.concat([df_old, pd.DataFrame(new_lists)], ignore_index=True)
        save_csv_safely(df_final, LISTS_FILE)
        print(f"✅ Added {len(new_lists)} new lists.")
    else:
        print("ℹ️ All lists are up to date.")


def get_tasks(session):
    """Extracts all problems from the main page and links them to lists."""
    print("🚀 Extracting all tasks from the main page...")
    
    response = session.get(CONTEST_URL)
    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.find('table', id='problems')
    
    if not table:
        print("❌ Could not find the problems table!")
        return

    rows = table.find_all('tr', attrs={'x-lists': True})
    all_tasks = []

    for tr in rows:
        list_ids = tr.get('x-lists').split()
        code_td = tr.find('td', class_='problem-code')
        name_td = tr.find('td', class_='problem-name')
        term_td = tr.find('td', class_='problem-term')
        
        if not code_td or not name_td:
            continue

        task_code = code_td.get_text(strip=True)
        title = name_td.get_text(strip=True)
        dates = term_td.get_text(separator='|', strip=True).split('|') if term_td else ["", ""]

        for lid in list_ids:
            all_tasks.append({
                'code': task_code,
                'title': title,
                'soft_deadline': dates[0] if len(dates) > 0 else "",
                'hard_deadline': dates[1] if len(dates) > 1 else "",
                'list_id': lid
            })

    df_tasks = pd.DataFrame(all_tasks)
    save_csv_safely(df_tasks, TASKS_FILE)
    print(f"✅ Success! Cataloged {len(all_tasks)} task-list assignments.")


def sync_submissions(session, students_dict):
    """Paginates through student submissions and incrementally downloads new ones."""
    print("📥 Syncing submissions...")
    ensure_data_dir()

    if not os.path.exists(PUBLIC_USERS_FILE):
        print("❌ public_users.csv is missing. Run student sync first.")
        return
        
    df_public = read_csv_safely(PUBLIC_USERS_FILE, ['hash', 'last_sync_date'])
    
    # Load or initialize submission file & IDs
    if not os.path.exists(SUBMISSIONS_FILE):
        with open(SUBMISSIONS_FILE, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(['submission_id', 'date', 'user_hash', 'task_code', 'result'])
        global_existing_ids = set()
    else:
        df_old = read_csv_safely(SUBMISSIONS_FILE, ['submission_id'])
        global_existing_ids = set(df_old['submission_id'].astype(str).tolist())

    for login, user_hash in students_dict.items():
        row_idx = df_public.index[df_public['hash'] == user_hash].tolist()
        if not row_idx: continue
        
        last_sync_str = str(df_public.at[row_idx[0], 'last_sync_date'])
        
        try:
            last_sync_date = datetime.strptime(last_sync_str, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            last_sync_date = datetime.strptime(DEFAULT_DATE, '%Y-%m-%d %H:%M:%S')
            
        print(f"🕵️ Syncing: {login}...", end=" ", flush=True)
        
        student_new_data = []
        page = 0
        keep_going = True
        new_max_date_obj = last_sync_date
        new_max_date_str = last_sync_str 

        while keep_going:
            status_url = f"{CONTEST_URL}/status/{login},/{page}"
            response = session.get(status_url)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            table = soup.find('table', id='status-table')
            if not table: break
                
            rows = table.find_all('tr')[1:]
            if not rows: break

            new_records_on_page = 0
            
            for tr in rows:
                cols = tr.find_all('td')
                if len(cols) < 6: continue
                
                div_status = cols[0].find('div')
                if not div_status or not div_status.get('title'): continue
                
                sub_id = re.search(r'\d+', div_status.get('title')).group()
                date_raw = cols[1].get_text(strip=True)
                
                try:
                    date_obj = datetime.strptime(date_raw, '%d-%m-%y %H:%M:%S')
                except ValueError:
                    continue

                if date_obj <= last_sync_date:
                    keep_going = False
                    continue

                if sub_id in global_existing_ids:
                    continue 

                task_link = cols[3].find('a', href=True)
                task_code = task_link['href'].split('/')[-1] if task_link else cols[3].get_text(strip=True)
                res_val = cols[5].get_text(strip=True)

                student_new_data.append([sub_id, date_obj.strftime('%Y-%m-%d %H:%M:%S'), user_hash, task_code, res_val])
                global_existing_ids.add(sub_id)
                new_records_on_page += 1
                
                if date_obj > new_max_date_obj:
                    new_max_date_obj = date_obj
                    new_max_date_str = date_obj.strftime('%Y-%m-%d %H:%M:%S')
            
            if new_records_on_page == 0 and last_sync_str != DEFAULT_DATE:
                break

            if keep_going:
                page += 1
                time.sleep(0.15)
            else:
                break

        if student_new_data:
            with open(SUBMISSIONS_FILE, 'a', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f, delimiter=';')
                writer.writerows(student_new_data)
                
            df_public.at[row_idx[0], 'last_sync_date'] = new_max_date_str
            save_csv_safely(df_public, PUBLIC_USERS_FILE)
            print(f"✅ (+{len(student_new_data)})")
        else:
            print("ℹ️ Up to date.")


# --- MAIN RUNNER ---
if __name__ == "__main__":
    session = get_session()
    if session:
        # 1. Structure cataloging
        get_lists(session)
        get_tasks(session)
        
        # 2. Sync students & get current username->hash mapping
        students_dict = sync_students_from_ranking(session)
        
        # 3. Fetch submissions
        if students_dict:
            sync_submissions(session, students_dict)
        
        print("🎉 Synchronization finished successfully!")