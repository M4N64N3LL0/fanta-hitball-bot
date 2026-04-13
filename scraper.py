import os
import json
import requests
import re
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore

# --- 1. CONFIGURAZIONE ---
ID_CAMPIONATI = [39, 41, 42, 43] 

QUOTE_ROSA_FEM = [
    "FEDERICA FUNNONE", "MARTINA LUPO", "SABRINA CAPITOLO", "ARIANNA VISMARA", 
    "SABRINA ZANFRETTA", "SARA SOTTOLANO", "MARTINA BRACESCO", "ROSSELLA DE BLASIO", 
    "CARLOTTA AMODEO", "FEDERICA AMORELLI", "ELENA PASINO", "MARA FERRARIS", 
    "ALICE LA VERSA", "NOEMI CASTELLUCCIO", "CHIARA GILARDI"
]

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

def inizializza_firebase():
    if not firebase_admin._apps:
        cred_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
        if cred_json:
            cred = credentials.Certificate(json.loads(cred_json))
        else:
            cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
    return firestore.client()

def carica_anagrafica_locale(percorso_file="giocatori.json"):
    try:
        with open(percorso_file, 'r', encoding='utf-8') as f:
            dati = json.load(f)
        mappa = {}
        for g in dati:
            nome_clean = re.sub(r'[^A-Z\s\']', '', g['nome_reale'].upper()).strip()
            mappa[nome_clean] = {
                'categoria': g['categoria'],
                'prezzo': g['prezzo'],
                'nome_originale': g['nome_reale']
            }
        return mappa
    except:
        return {}

# --- 2. LOGICA CALCOLO PUNTI ---
def calcola_punteggio_fanta(hits, autohits, fatti, subiti, giallo, rosso, is_fem, perso_tavolino):
    # Ora 'hits' sono i tiri fisici totali
    punti_base = (hits * 2) if is_fem else hits
    malus_autohit = -(autohits * 1)
    
    bonus_fatti = 5 if fatti >= 76 else (2 if fatti >= 51 else 0)
    
    if subiti <= 50: bonus_subiti = 5
    elif subiti <= 75: bonus_subiti = 2
    elif subiti >= 101: bonus_subiti = -5
    else: bonus_subiti = 0
    
    malus_disc = (-10 if giallo else 0) + (-20 if rosso else 0) + (-20 if perso_tavolino else 0)
    
    return punti_base + malus_autohit + bonus_fatti + bonus_subiti + malus_disc

# --- 3. ANALISI REFERTO ---
def processa_referto(url, giornata, tot_casa, tot_trasf, db, mappa_giocatori):
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        tavolino_casa = (tot_casa == 0 and soup.find(string=re.compile(r'vinta a tavolino', re.I)))
        tavolino_trasf = (tot_trasf == 0 and soup.find(string=re.compile(r'vinta a tavolino', re.I)))

        liste_squadre = soup.find_all('ul', class_=re.compile(r'list-group', re.I))
        giocatori_match = []

        def estrai_da_lista(ul_node, is_casa):
            for li in ul_node.find_all('li', class_=re.compile(r'list-group-item', re.I)):
                testo = li.get_text(separator=' ', strip=True)
                if "x" not in testo: continue

                nome_raw = re.split(r'\d+\s*x|Tot\.', testo, flags=re.I)[0].strip()
                nome_clean = re.sub(r'[^A-Z\s\']', '', nome_raw.upper()).strip()
                if len(nome_clean) < 3: continue

                # NUOVA LOGICA: Somma tutti i numeri prima delle 'x' (es: "2 x2 3 x3" -> 2+3 = 5 hit)
                tiri_fisici = 0
                matches_hit = re.findall(r'(\d+)\s*x\s*(?:2|3)', testo)
                for m in matches_hit:
                    tiri_fisici += int(m)
                
                m_auto = re.search(r'(\d+)\s*x\s*AUTOHIT', testo, re.I)
                autohits = int(m_auto.group(1)) if m_auto else 0

                giallo = li.find(class_=re.compile(r'warning|yellow', re.I)) is not None
                rosso = li.find(class_=re.compile(r'danger|red', re.I)) is not None

                giocatori_match.append({
                    "nome": nome_clean, "hits": tiri_fisici, "autohits": autohits, 
                    "giallo": giallo, "rosso": rosso, "is_casa": is_casa
                })

        if len(liste_squadre) >= 2:
            estrai_da_lista(liste_squadre[0], is_casa=True)
            estrai_da_lista(liste_squadre[1], is_casa=False)

        for g in giocatori_match:
            nome_db = g['nome']
            if nome_db not in mappa_giocatori: continue 

            doc_ref = db.collection('giocatori').document(nome_db)
            # Rimuovi i # qui sotto quando hai finito di correggere i dati vecchi
            # doc = doc_ref.get()
            # if doc.exists and 'punti_giornate' in doc.to_dict() and str(giornata) in doc.to_dict()['punti_giornate']: continue

            is_fem = nome_db in QUOTE_ROSA_FEM
            fatti = tot_casa if g['is_casa'] else tot_trasf
            subiti = tot_trasf if g['is_casa'] else tot_casa
            ha_perso_tavolino = (g['is_casa'] and tavolino_casa) or (not g['is_casa'] and tavolino_trasf)

            voto = calcola_punteggio_fanta(g['hits'], g['autohits'], fatti, subiti, g['giallo'], g['rosso'], is_fem, ha_perso_tavolino)
            
            db.collection('giocatori').document(nome_db).set({
                'nome': mappa_giocatori[nome_db]['nome_originale'],
                'categoria': mappa_giocatori[nome_db]['categoria'],
                'prezzo': mappa_giocatori[nome_db]['prezzo'],
                'punti_giornate': {str(giornata): voto}
            }, merge=True)
            print(f"  -> {nome_db} (G{giornata}): {voto} pt ({g['hits']} hit)")

    except Exception as e:
        print(f"Errore: {e}")

def recupera_e_analizza(db, mappa_giocatori):
    for camp_id in ID_CAMPIONATI:
        url_camp = f"https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id={camp_id}"
        try:
            res = requests.get(url_camp, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(res.text, 'html.parser')
            for a_tag in soup.find_all('a', href=True):
                if 'match_id=' in a_tag['href'] or 'referto_id=' in a_tag['href']:
                    riga = a_tag
                    for _ in range(5):
                        if riga.parent:
                            riga = riga.parent
                            if "Risultato:" in riga.get_text(): break
                    m = re.search(r'Risultato:\s*(\d+)\s*-\s*(\d+)', riga.get_text())
                    if m:
                        giornata = 1
                        curr = a_tag
                        while curr:
                            curr = curr.find_previous(['h1', 'h2', 'h3', 'h4', 'strong'])
                            if curr:
                                mg = re.search(r'Giornata\s+(\d+)', curr.get_text(), re.I)
                                if mg: 
                                    giornata = int(mg.group(1))
                                    break
                        processa_referto("https://referto.plvhitball.it/" + a_tag['href'].lstrip('/'), giornata, int(m.group(1)), int(m.group(2)), db, mappa_giocatori)
        except: pass

if __name__ == "__main__":
    mappa = carica_anagrafica_locale()
    if mappa:
        db_firestore = inizializza_firebase()
        recupera_e_analizza(db_firestore, mappa)
