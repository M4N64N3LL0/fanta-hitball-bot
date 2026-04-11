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
        print(f"Errore Firebase: {e}")
        return None

def calcola_bonus_squadra(p_fatti, p_subiti):
    bonus = 0.0
    if 51 <= p_fatti <= 75: bonus += 2.0
    elif p_fatti >= 76: bonus += 3.0
    if p_subiti <= 50: bonus += 5.0
    elif 51 <= p_subiti <= 75: bonus += 2.0
    elif p_subiti >= 101: bonus -= 5.0
    return bonus

def estrai_valori_dal_li(li_tag):
    """
    Risolve il bug delle icone: converte le icone della X (fa-times) in vero testo
    così il bot non perde i moltiplicatori!
    """
    # 1. Troviamo tutte le icone
    for icona in li_tag.find_all('i'):
        classi = icona.get('class', [])
        # Se è l'icona della moltiplicazione (una X visiva)
        if 'fa-times' in classi or 'fa-close' in classi:
            icona.replace_with(" X ") # Sostituiamo il disegno con la lettera X
            
    # 2. Ora possiamo estrarre tutto il testo in modo sicuro
    testo = li_tag.get_text(separator=" ", strip=True).upper()
    
    h2, h3, ah = 0, 0, 0
    
    # Cerchiamo: un numero, seguito da eventuali spazi, una X, eventuali spazi, e un 2
    m2 = re.findall(r'(\d+)\s*X\s*2', testo)
    if m2: h2 = sum(int(x) for x in m2)
    
    m3 = re.findall(r'(\d+)\s*X\s*3', testo)
    if m3: h3 = sum(int(x) for x in m3)
    
    mah = re.findall(r'(\d+)\s*AUTOHIT', testo)
    if mah: ah = sum(int(x) for x in mah)
    
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
        print(f"Scansione: {cat_nome}")
        try:
            r = requests.get(url)
            soup = BeautifulSoup(r.text, 'html.parser')
        except:
            continue
        
        tabelle = soup.find_all('div', class_='table-responsive')
        for tab in tabelle:
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
                        dati = []
                        for it in items:
                            # 1. Salva il nome prima di fare manipolazioni HTML
                            nome = list(it.stripped_strings)[0] if list(it.stripped_strings) else "Ignoto"
                            
                            # 2. Conta ammonizioni/espulsioni tramite classi CSS
                            amm = len(it.find_all('i', class_='text-warning'))
                            esp = len(it.find_all('i', class_='text-danger'))
                            
                            # 3. Estrai punti (questa funzione decifra l'enigma della X)
                            h2, h3, ah = estrai_valori_dal_li(it)
                            
                            p_h_squadra += (h2 * 2) + (h3 * 3)
                            ah_squadra += ah
                            
                            dati.append({'nome': nome, 'h2': h2, 'h3': h3, 'ah': ah, 'amm': amm, 'esp': esp})
                        return p_h_squadra, ah_squadra, dati

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
                except Exception as e:
                    print(f"Errore in {l}: {e}")
                    continue

    print("Calcolo punti totali e salvataggio in corso...")
    batch = db.batch()
    for n, d in giocatori_db.items():
        d["punteggio_campionato"] = sum(d["punti_giornate"].values())
        # Ricalcola il prezzo dinamicamente basato sui punti veri
        d["prezzo"] = max(10, int(d["punteggio_campionato"] * 2))
        d["nome_reale"] = n
        d["nome_visualizzato"] = n
        batch.set(db.collection("giocatori").document(n), d, merge=True)
    batch.commit()
    print("AGGIORNAMENTO FIREBASE COMPLETATO")

if __name__ == "__main__": 
    scraper_professionale()
