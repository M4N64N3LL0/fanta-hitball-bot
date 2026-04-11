import requests
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore
import urllib.parse
import os
import json
import re

def avvia_firebase():
    if firebase_admin._apps:
        return firestore.client()
    firebase_secret = os.environ.get("FIREBASE_KEY")
    try:
        if firebase_secret:
            cred_dict = json.loads(firebase_secret)
            cred = credentials.Certificate(cred_dict)
        else:
            cred = credentials.Certificate("chiave.json")
        firebase_admin.initialize_app(cred)
        return firestore.client()
    except Exception as e:
        print(f"Errore Firebase: {e}")
        return None

def calcola_bonus_squadra(punti_fatti, punti_subiti):
    bonus = 0.0
    if 51 <= punti_fatti <= 75: bonus += 2.0
    elif punti_fatti >= 76: bonus += 3.0
    
    if punti_subiti <= 50: bonus += 5.0
    elif 51 <= punti_subiti <= 75: bonus += 2.0
    elif punti_subiti >= 101: bonus -= 5.0
    return bonus

def estrai_dati_squadra(team_list):
    h2_tot, h3_tot, ah_tot = 0, 0, 0
    giocatori_html = team_list.find_all('li', class_='list-group-item')
    for blocco in giocatori_html:
        testi = list(blocco.stripped_strings)
        # Cerchiamo i pattern x2, x3 e AUTOHIT nel testo del giocatore
        for t in testi:
            t_pulito = t.upper().replace(' ', '')
            if 'X2' in t_pulito:
                val = re.findall(r'(\d+)X2', t_pulito)
                if val: h2_tot += int(val[0])
            if 'X3' in t_pulito:
                val = re.findall(r'(\d+)X3', t_pulito)
                if val: h3_tot += int(val[0])
            if 'AUTOHIT' in t_pulito:
                val = re.findall(r'(\d+)AUTOHIT', t_pulito)
                if val: ah_tot += int(val[0])
    return (h2_tot * 2) + (h3_tot * 3), ah_tot, giocatori_html

def scraper_professionale():
    db = avvia_firebase()
    if not db: return 

    campionati = {
        "A1": "https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id=39",
        "A2": "https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id=41",
        "B1": "https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id=42",
        "B2": "https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id=43",
        "FEM": "https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id=47"
    }

    lista_femm = ["Federica Funnone", "Martina Lupo", "Sabrina Capitolo", "Arianna Vismara", "Sabrina Zanfretta", "Sara Sottolano", "Martina Bracesco", "Rossella De Blasio", "Carlotta Amodeo", "Federica Amorelli", "Elena Pasino", "Mara Ferraris", "Alice La Versa", "Noemi Castelluccio", "Chiara Gilardi"]
    giocatori_data = {}

    for cat_nome, url in campionati.items():
        print(f"Analisi campionato: {cat_nome}")
        r = requests.get(url)
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # Cerchiamo tutti i titoli delle giornate (h3 o h4)
        titoli_giornate = soup.find_all(['h3', 'h4'])
        
        for titolo in titoli_giornate:
            testo_titolo = titolo.get_text()
            match_g = re.search(r'Giornata\s+(\d+)', testo_titolo, re.I)
            if not match_g: continue
            
            g_id = match_g.group(1)
            # La tabella dei referti è l'elemento successivo al titolo
            tabella = titolo.find_next('div', class_='table-responsive')
            if not tabella: continue
            
            links = [urllib.parse.urljoin("https://referto.plvhitball.it/", a['href']) for a in tabella.find_all('a', href=True) if 'route=match/result' in a['href']]
            
            for l in links:
                try:
                    rp = requests.get(l)
                    sp = BeautifulSoup(rp.text, 'html.parser')
                    team_lists = sp.find_all('ul', class_='list-group')
                    
                    if len(team_lists) >= 2:
                        p_A_h, ah_A, g_A = estrai_dati_squadra(team_lists[0])
                        p_B_h, ah_B, g_B = estrai_dati_squadra(team_lists[1])
                        
                        sc_A, sc_B = p_A_h + ah_B, p_B_h + ah_A
                        tav_A = -20.0 if (p_A_h == 0 and "tavolino" in sp.text.lower()) else 0.0
                        tav_B = -20.0 if (p_B_h == 0 and "tavolino" in sp.text.lower()) else 0.0

                        bon_A, bon_B = calcola_bonus_squadra(sc_A, sc_B), calcola_bonus_squadra(sc_B, sc_A)
                        
                        for lista_g, bonus_t, malus_t in [(g_A, bon_A, tav_A), (g_B, bon_B, tav_B)]:
                            for item in lista_g:
                                info = list(item.stripped_strings)
                                if not info: continue
                                nome = info[0]
                                
                                # Calcolo singoli hit per questo giocatore
                                h2, h3, ah, amm, esp = 0, 0, 0, 0, 0
                                for t in info:
                                    t_u = t.upper().replace(' ','')
                                    if 'X2' in t_u: h2 = int(re.findall(r'(\d+)X2', t_u)[0])
                                    if 'X3' in t_u: h3 = int(re.findall(r'(\d+)X3', t_u)[0])
                                    if 'AUTOHIT' in t_u: ah = int(re.findall(r'(\d+)AUTOHIT', t_u)[0])
                                
                                amm = len(item.find_all('i', class_='text-warning'))
                                esp = len(item.find_all('i', class_='text-danger'))
                                molt = 2.0 if nome in lista_femm else 1.0
                                
                                p_giocatore = ((h2 + h3) * molt) - ah - (amm * 10) - (esp * 20)
                                totale_giornata = p_giocatore + bonus_t + malus_t

                                if nome not in giocatori_data:
                                    giocatori_data[nome] = {"punti_giornate": {}}
                                
                                giocatori_data[nome]["punti_giornate"][g_id] = totale_giornata
                except: continue

    # Calcolo dei totali e caricamento
    print("Salvataggio dati...")
    batch = db.batch()
    for nome, data in giocatori_data.items():
        punti_map = data["punti_giornate"]
        totale_camp = sum(punti_map.values())
        data["punteggio_campionato"] = totale_camp
        
        doc_ref = db.collection("giocatori").document(nome)
        batch.set(doc_ref, data, merge=True)
        
    batch.commit()
    print("=== AGGIORNAMENTO COMPLETATO CON SUCCESSO ===")

if __name__ == "__main__":
    scraper_professionale()
