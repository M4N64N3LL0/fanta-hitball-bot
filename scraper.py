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

# --- 2. LOGICA CALCOLO PUNTI FANTAHITBALL ---
def calcola_punteggio_fanta(hits, autohits, fatti, subiti, giallo, rosso, is_fem, perso_tavolino):
    punti_base = (hits * 2) if is_fem else hits
    malus_autohit = -(autohits * 1)
    
    bonus_fatti = 5 if fatti >= 76 else (2 if fatti >= 51 else 0)
    
    if subiti <= 50: bonus_subiti = 5
    elif subiti <= 75: bonus_subiti = 2
    elif subiti >= 101: bonus_subiti = -5
    else: bonus_subiti = 0
    
    malus_disc = (-10 if giallo else 0) + (-20 if rosso else 0) + (-20 if perso_tavolino else 0)
    
    return punti_base + malus_autohit + bonus_fatti + bonus_subiti + malus_disc

# --- 3. ANALISI DEL SINGOLO REFERTO (IL "RECINTO") ---
def processa_referto(url, giornata, tot_casa, tot_trasf, db):
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # Check Sconfitta a Tavolino
        tavolino_casa = False
        tavolino_trasf = False
        alert_tavolino = soup.find(string=re.compile(r'vinta a tavolino', re.I))
        if alert_tavolino:
            if tot_casa == 0: tavolino_casa = True
            if tot_trasf == 0: tavolino_trasf = True

        li_tags = soup.find_all('li', class_=re.compile(r'list-group-item', re.I))
        giocatori_match = []

        for li in li_tags:
            testo = li.get_text(separator=' ', strip=True)
            
            if "Tot." not in testo and "AUTOHIT" not in testo:
                continue

            nome_raw = re.split(r'\d+\s*x|Tot\.', testo, flags=re.I)[0].strip()
            nome_clean = re.sub(r'[^A-Z\s\']', '', nome_raw.upper()).strip()
            
            if len(nome_clean) < 3: 
                continue

            m_hits = re.search(r'Tot\.\s*(\d+)', testo, re.I)
            hits = int(m_hits.group(1)) if m_hits else 0
            
            m_auto = re.search(r'(\d+)\s*x\s*AUTOHIT', testo, re.I)
            autohits = int(m_auto.group(1)) if m_auto else 0

            giallo = li.find(class_=re.compile(r'warning|yellow', re.I)) is not None
            rosso = li.find(class_=re.compile(r'danger|red', re.I)) is not None

            giocatori_match.append({
                "nome": nome_clean, "hits": hits, "autohits": autohits, 
                "giallo": giallo, "rosso": rosso
            })

        mezzo = len(giocatori_match) / 2
        for idx, g in enumerate(giocatori_match):
            doc_ref = db.collection('giocatori').document(g['nome'])
            doc = doc_ref.get()
            
            if not doc.exists:
                continue 
                
            dati = doc.to_dict() or {}
            
            if 'punti_giornate' in dati and str(giornata) in dati['punti_giornate']:
                continue 

            is_fem = (g['nome'] in QUOTE_ROSA_FEM) or (dati.get('categoria') == "FEM")
            is_casa = (idx < mezzo)
            
            fatti = tot_casa if is_casa else tot_trasf
            subiti = tot_trasf if is_casa else tot_casa
            ha_perso_tavolino = (is_casa and tavolino_casa) or (not is_casa and tavolino_trasf)

            voto = calcola_punteggio_fanta(g['hits'], g['autohits'], fatti, subiti, g['giallo'], g['rosso'], is_fem, ha_perso_tavolino)
            
            doc_ref.set({'punti_giornate': {str(giornata): voto}}, merge=True)
            print(f"  -> Salvato in DB: {g['nome']} (G{giornata}): {voto} pt")

    except Exception as e:
        print(f"Errore referto {url}: {e}")

# --- 4. CRAWLER ---
def recupera_e_analizza(db):
    for camp_id in ID_CAMPIONATI:
        url_camp = f"https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id={camp_id}"
        print(f"\n{'='*40}\n>>> SCANSIONE CAMPIONATO ID: {camp_id}\n{'='*40}")
        
        try:
            res = requests.get(url_camp, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(res.text, 'html.parser')
            
            referti_analizzati = 0
            
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href']
                
                if 'match_id=' in href or 'referto_id=' in href:
                    
                    # CORREZIONE: Saliamo nell'HTML finché non troviamo tutta la riga col risultato
                    riga = a_tag
                    testo_riga = ""
                    for _ in range(5):
                        if riga.parent:
                            riga = riga.parent
                            testo_riga = riga.get_text(separator=' ', strip=True)
                            if re.search(r'Risultato:\s*\d+\s*-\s*\d+', testo_riga):
                                break
                    
                    match_ris = re.search(r'Risultato:\s*(\d+)\s*-\s*(\d+)', testo_riga)
                    if not match_ris:
                        continue 
                        
                    tot_casa = int(match_ris.group(1))
                    tot_trasf = int(match_ris.group(2))
                    
                    giornata = 1
                    curr = a_tag
                    while curr:
                        curr = curr.find_previous(['h1', 'h2', 'h3', 'h4', 'div', 'strong', 'b'])
                        if curr:
                            t = curr.get_text(strip=True)
                            m = re.search(r'(?:Giornata\s+(\d+))|(?:(\d+)[\^°a-z]*\s+Giornata)', t, re.IGNORECASE)
                            if m:
                                giornata = int(m.group(1) or m.group(2))
                                break
                    
                    url_ref = "https://referto.plvhitball.it/" + href.lstrip('/') if not href.startswith('http') else href
                    referti_analizzati += 1
                    processa_referto(url_ref, giornata, tot_casa, tot_trasf, db)
                    
            print(f"[OK] Analizzati {referti_analizzati} referti utili per il Camp. {camp_id}.")
                    
        except Exception as e:
            print(f"Errore Campionato {camp_id}: {e}")

if __name__ == "__main__":
    print("Avvio FantaHitball Bot...")
    db_firestore = inizializza_firebase()
    recupera_e_analizza(db_firestore)
    print("\n=== AGGIORNAMENTO COMPLETATO ===")
