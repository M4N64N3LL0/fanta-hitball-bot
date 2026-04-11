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

def calcola_bonus_squadra(p_fatti, p_subiti):
    bonus = 0.0
    if 51 <= p_fatti <= 75: bonus += 2.0
    elif p_fatti >= 76: bonus += 3.0
    if p_subiti <= 50: bonus += 5.0
    elif 51 <= p_subiti <= 75: bonus += 2.0
    elif p_subiti >= 101: bonus -= 5.0
    return bonus

def estrai_valori(testo_riga):
    """Estrae h2, h3, autohit analizzando ogni singola parola della riga"""
    h2, h3, ah = 0, 0, 0
    testo = testo_riga.upper().replace(" ", "")
    
    # Regex migliorata: cerca cifre seguite dal tipo di punto
    r2 = re.search(r'(\d+)X2', testo)
    if r2: h2 = int(r2.group(1))
    
    r3 = re.search(r'(\d+)X3', testo)
    if r3: h3 = int(r3.group(1))
    
    rah = re.search(r'(\d+)AUTOHIT', testo)
    if rah: ah = int(rah.group(1))
    
    return h2, h3, ah

def scraper_professionale():
    db = avvia_firebase()
    if not db: return 

    campionati = {
        "A1": "39", "A2": "41", "B1": "42", "B2": "43", "FEM": "47"
    }

    lista_femm = ["Federica Funnone", "Martina Lupo", "Sabrina Capitolo", "Arianna Vismara", "Sabrina Zanfretta", "Sara Sottolano", "Martina Bracesco", "Rossella De Blasio", "Carlotta Amodeo", "Federica Amorelli", "Elena Pasino", "Mara Ferraris", "Alice La Versa", "Noemi Castelluccio", "Chiara Gilardi"]
    giocatori_db = {}

    for cat_nome, c_id in campionati.items():
        url = f"https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id={c_id}"
        print(f"Analisi: {cat_nome}")
        r = requests.get(url)
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # Trova tutte le tabelle (giornate)
        tabelle = soup.find_all('div', class_='table-responsive')
        for tab in tabelle:
            # Trova il titolo della giornata sopra la tabella
            h_titolo = tab.find_previous(['h3', 'h4', 'b'])
            match_g = re.search(r'Giornata\s+(\d+)', h_titolo.get_text() if h_titolo else "1", re.I)
            g_id = match_g.group(1) if match_g else "1"
            
            links = [urllib.parse.urljoin("https://referto.plvhitball.it/", a['href']) for a in tab.find_all('a', href=True) if 'route=match/result' in a['href']]
            
            for l in links:
                try:
                    rp = requests.get(l)
                    sp = BeautifulSoup(rp.text, 'html.parser')
                    uls = sp.find_all('ul', class_='list-group')
                    if len(uls) < 2: continue

                    def processa_team(ul):
                        p_h_squadra = 0
                        ah_squadra = 0
                        items = ul.find_all('li', class_='list-group-item')
                        dati_giocatori = []
                        for it in items:
                            # Prende TUTTO il testo dentro il blocco del giocatore
                            testo_completo = it.get_text(separator=" ").strip()
                            nome = list(it.stripped_strings)[0]
                            h2, h3, ah = estrai_valori(testo_completo)
                            
                            p_h_squadra += (h2 * 2) + (h3 * 3)
                            ah_squadra += ah
                            
                            amm = len(it.find_all('i', class_='text-warning'))
                            esp = len(it.find_all('i', class_='text-danger'))
                            
                            dati_giocatori.append({
                                'nome': nome, 'h2': h2, 'h3': h3, 'ah': ah, 'amm': amm, 'esp': esp
                            })
                        return p_h_squadra, ah_squadra, dati_giocatori

                    phA, ahA, gA = processa_team(uls[0])
                    phB, ahB, gB = processa_team(uls[1])

                    scA, scB = phA + ahB, phB + ahA
                    bonA = calcola_bonus_squadra(scA, scB)
                    bonB = calcola_bonus_squadra(scB, scA)

                    for lista, b_t in [(gA, bonA), (gB, bonB)]:
                        for g in lista:
                            molt = 2.0 if g['nome'] in lista_femm else 1.0
                            p_indiv = ((g['h2'] + g['h3']) * molt) - g['ah'] - (g['amm'] * 10) - (g['esp'] * 20)
                            p_tot = p_indiv + b_t
                            
                            if g['nome'] not in giocatori_db:
                                giocatori_db[g['nome']] = {"punti_giornate": {}, "categoria": cat_nome}
                            giocatori_db[g['nome']]["punti_giornate"][g_id] = p_tot
                except: continue

    batch = db.batch()
    for n, d in giocatori_db.items():
        d["punteggio_campionato"] = sum(d["punti_giornate"].values())
        d["prezzo"] = max(10, int(d["punteggio_campionato"] * 2))
        d["nome_reale"] = n
        d["nome_visualizzato"] = n
        batch.set(db.collection("giocatori").document(n), d, merge=True)
    batch.commit()
    print("DATABASE AGGIORNATO!")

if __name__ == "__main__": scraper_professionale()
