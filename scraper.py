import requests
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore
import urllib.parse
import os
import json
import re

def avvia_firebase():
    if firebase_admin._apps: return firestore.client()
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
        print(f"Errore Firebase: {e}"); return None

def calcola_bonus_squadra(punti_fatti, punti_subiti):
    bonus = 0.0
    if 51 <= punti_fatti <= 75: bonus += 2.0
    elif punti_fatti >= 76: bonus += 3.0
    if punti_subiti <= 50: bonus += 5.0
    elif 51 <= punti_subiti <= 75: bonus += 2.0
    elif punti_subiti >= 101: bonus -= 5.0
    return bonus

def estrai_numeri_da_testo(lista_testi):
    """Estrae con precisione chirurgica h2, h3 e autohit dalla riga del giocatore"""
    h2, h3, ah = 0, 0, 0
    testo_unito = " ".join(lista_testi).upper().replace(" ", "")
    
    # Cerca numeri seguiti da X2 (es: 9X2)
    m2 = re.findall(r'(\d+)X2', testo_unito)
    if m2: h2 = sum(int(x) for x in m2)
    
    # Cerca numeri seguiti da X3 (es: 1X3)
    m3 = re.findall(r'(\d+)X3', testo_unito)
    if m3: h3 = sum(int(x) for x in m3)
    
    # Cerca numeri seguiti da AUTOHIT (es: 2AUTOHIT)
    mah = re.findall(r'(\d+)AUTOHIT', testo_unito)
    if mah: ah = sum(int(x) for x in mah)
    
    return h2, h3, ah

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
    giocatori_db = {}

    for cat_nome, url in campionati.items():
        print(f"Scansione: {cat_nome}")
        r = requests.get(url)
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # Cerchiamo tutte le sezioni delle giornate
        sezioni = soup.find_all(['h3', 'h4', 'b'])
        for s in sezioni:
            match_g = re.search(r'Giornata\s+(\d+)', s.get_text(), re.I)
            if not match_g: continue
            g_id = match_g.group(1)
            
            tabella = s.find_next('div', class_='table-responsive')
            if not tabella: continue
            
            links = [urllib.parse.urljoin("https://referto.plvhitball.it/", a['href']) for a in tabella.find_all('a', href=True) if 'route=match/result' in a['href']]
            
            for l in links:
                try:
                    rp = requests.get(l)
                    sp = BeautifulSoup(rp.text, 'html.parser')
                    team_uls = sp.find_all('ul', class_='list-group')
                    if len(team_uls) < 2: continue

                    # Calcolo punti totali squadra per i bonus
                    def get_stats_squadra(ul):
                        p_h = 0
                        ah_squadra = 0
                        giocatori = ul.find_all('li', class_='list-group-item')
                        for g in giocatori:
                            h2, h3, ah = estrai_numeri_da_testo(list(g.stripped_strings))
                            p_h += (h2 * 2) + (h3 * 3)
                            ah_squadra += ah
                        return p_h, ah_squadra, giocatori

                    pA_h, ahA, gA = get_stats_squadra(team_uls[0])
                    pB_h, ahB, gB = get_stats_squadra(team_uls[1])

                    scA, scB = pA_h + ahB, pB_h + ahA
                    bonA, bonB = calcola_bonus_squadra(scA, scB), calcola_bonus_squadra(scB, scA)
                    tavA = -20.0 if (pA_h == 0 and "tavolino" in sp.text.lower()) else 0.0
                    tavB = -20.0 if (pB_h == 0 and "tavolino" in sp.text.lower()) else 0.0

                    for lista_g, b_team, m_tav in [(gA, bonA, tavA), (gB, bonB, tavB)]:
                        for item in lista_g:
                            info = list(item.stripped_strings)
                            if not info: continue
                            nome = info[0]
                            h2, h3, ah = estrai_numeri_da_testo(info)
                            
                            amm = len(item.find_all('i', class_='text-warning'))
                            esp = len(item.find_all('i', class_='text-danger'))
                            molt = 2.0 if nome in lista_femm else 1.0
                            
                            p_base = ((h2 + h3) * molt) - ah - (amm * 10) - (esp * 20)
                            p_finale = p_base + b_team + m_tav

                            if nome not in giocatori_db: 
                                giocatori_db[nome] = {"punti_giornate": {}, "categoria": cat_nome}
                            
                            giocatori_db[nome]["punti_giornate"][g_id] = p_finale
                except: continue

    print("Aggiornamento Firebase...")
    batch = db.batch()
    for nome, dati in giocatori_db.items():
        dati["punteggio_campionato"] = sum(dati["punti_giornate"].values())
        dati["nome_reale"] = nome
        dati["nome_visualizzato"] = nome
        # Calcolo prezzo dinamico (minimo 10)
        dati["prezzo"] = max(10, int(dati["punteggio_campionato"] * 2))
        
        doc_ref = db.collection("giocatori").document(nome)
        batch.set(doc_ref, dati, merge=True)
    batch.commit()
    print("FINITO!")

if __name__ == "__main__":
    scraper_professionale()
