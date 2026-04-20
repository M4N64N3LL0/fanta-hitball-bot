import os
import json
import requests
import re
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore

# --- 1. CONFIGURAZIONE ---
ID_CAMPIONATI = [39, 41, 42, 43] 
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

QUOTE_ROSA_FEM = [
    "FEDERICA FUNNONE", "MARTINA LUPO", "SABRINA CAPITOLO", "ARIANNA VISMARA", 
    "SABRINA ZANFRETTA", "SARA SOTTOLANO", "MARTINA BRACESCO", "ROSSELLA DE BLASIO", 
    "CARLOTTA AMODEO", "FEDERICA AMORELLI", "ELENA PASINO", "MARA FERRARIS", 
    "ALICE LA VERSA", "NOEMI CASTELLUCCIO", "CHIARA GILARDI"
]

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
            n_raw = g['nome_reale'].upper()
            n_clean = re.sub(r'[^A-Z\s\']', '', n_raw).strip()
            mappa[n_clean] = {
                'categoria': g['categoria'],
                'prezzo': g['prezzo'],
                'nome_originale': g['nome_reale']
            }
        return mappa
    except:
        return {}

def scarica_stato_firebase(db):
    print(">>> Recupero stato attuale da Firebase in RAM...")
    stato = {}
    docs = db.collection('giocatori').stream()
    for doc in docs:
        stato[doc.id] = doc.to_dict().get('punti_giornate', {})
    return stato

def calcola_punteggio_fanta(hits, autohits, fatti, subiti, giallo, rosso, is_fem, tav):
    p_base = (hits * 2) if is_fem else hits
    malus_auto = -(autohits * 1)
    bonus_att = 5 if fatti >= 76 else (2 if fatti >= 51 else 0)
    if subiti <= 50: bonus_def = 5
    elif subiti <= 75: bonus_def = 2
    elif subiti >= 101: bonus_def = -5
    else: bonus_def = 0
    malus_disc = (-10 if giallo else 0) + (-20 if rosso else 0) + (-20 if tav else 0)
    return p_base + malus_auto + bonus_att + bonus_def + malus_disc

def processa_referto(url, giornata, tot_casa, tot_trasf, db, mappa, stato_db):
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        tavolino = soup.find(string=re.compile(r'vinta a tavolino', re.I)) is not None
        liste_squadre = soup.find_all('ul', class_=re.compile(r'list-group', re.I))
        
        if len(liste_squadre) < 2: 
            print(f"      [!] Attenzione: Referto G{giornata} strutturato male o incompleto.")
            return

        giocatori_match = []
        def estrai(ul_node, is_casa):
            for li in ul_node.find_all('li', class_=re.compile(r'list-group-item', re.I)):
                testo = li.get_text(separator=' ', strip=True)
                if "x" not in testo: continue
                n_raw = re.split(r'\d+\s*x|Tot\.', testo, flags=re.I)[0].strip().upper()
                n_clean = re.sub(r'[^A-Z\s\']', '', n_raw).strip()
                if len(n_clean) < 3: continue
                tiri = sum(int(n) for n in re.findall(r'(\d+)\s*x\s*(?:2|3)', testo))
                m_auto = re.search(r'(\d+)\s*x\s*AUTOHIT', testo, re.I)
                autohits = int(m_auto.group(1)) if m_auto else 0
                giallo = li.find(class_=re.compile(r'warning|yellow', re.I)) is not None
                rosso = li.find(class_=re.compile(r'danger|red', re.I)) is not None
                giocatori_match.append({"nome": n_clean, "hits": tiri, "autohits": autohits, "giallo": giallo, "rosso": rosso, "is_casa": is_casa})

        estrai(liste_squadre[0], True)
        estrai(liste_squadre[1], False)

        if not giocatori_match:
            print(f"      [!] Referto G{giornata} aperto, ma non ci sono le statistiche dei giocatori!")
            return

        for g in giocatori_match:
            if g['nome'] not in mappa: 
                continue
            
            # ---> HO RIMOSSO IL FRENO SICURO <---
            # Ora se il giocatore è nella mappa locale, sovrascriverà sempre il voto su Firebase

            is_fem = g['nome'] in QUOTE_ROSA_FEM
            fatti = tot_casa if g['is_casa'] else tot_trasf
            subiti = tot_trasf if g['is_casa'] else tot_casa
            tav_match = tavolino and ((g['is_casa'] and tot_casa == 0) or (not g['is_casa'] and tot_trasf == 0))
            voto = calcola_punteggio_fanta(g['hits'], g['autohits'], fatti, subiti, g['giallo'], g['rosso'], is_fem, tav_match)
            
            db.collection('giocatori').document(g['nome']).set({
                'nome': mappa[g['nome']]['nome_originale'],
                'categoria': mappa[g['nome']]['categoria'],
                'prezzo': mappa[g['nome']]['prezzo'],
                f'punti_giornate.{giornata}': voto
            }, merge=True)
            
            if g['nome'] not in stato_db: stato_db[g['nome']] = {}
            stato_db[g['nome']][str(giornata)] = voto
            print(f"      [OK] Scritto/Aggiornato {g['nome']} | G{giornata}: {voto}pt")

    except Exception as e:
        print(f"      [ERR] {e}")

def recupera_e_analizza(db, mappa, stato_db):
    cat_map = {39: "A1", 41: "A2", 42: "B1", 43: "B2"}

    for camp_id in ID_CAMPIONATI:
        cat_label = cat_map.get(camp_id, "")
        url_camp = f"https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id={camp_id}"
        print(f"\n>>> SCANSIONE {cat_label} (ID: {camp_id})")
        
        try:
            res = requests.get(url_camp, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(res.text, 'html.parser')
            links = [a for a in soup.find_all('a', href=True) if 'match_id=' in a['href'] or 'referto_id=' in a['href']]
            
            for i, a_tag in enumerate(links, 1):
                
                # --- SCUDO PARTITE LIVE ---
                testo_tasto = a_tag.get_text(strip=True).lower()
                if "live" in testo_tasto or "in corso" in testo_tasto:
                    print(f"   [{i}/{len(links)}] Partita LIVE ignorata (evito voti parziali)")
                    continue

                # Trova giornata
                giornata = 1
                curr = a_tag
                while curr:
                    curr = curr.find_previous(['h1', 'h2', 'h3', 'h4', 'strong', 'b', 'div'])
                    if curr:
                        t_g = curr.get_text(strip=True)
                        mg = re.search(r'(\d+)[\^°\s]*Giornata|Giornata\s+(\d+)', t_g, re.I)
                        if mg:
                            giornata = int(mg.group(1) or mg.group(2))
                            break
                
                # ==========================================
                # --- FILTRO GIORNATE (16, 17, 18) ---
                # ==========================================
                if giornata not in [16, 17, 18]:
                    print(f"   [{i}/{len(links)}] Salto G{giornata} (Fuori range 16-18)")
                    continue
                # ==========================================
                
                riga = a_tag
                for _ in range(5):
                    if riga.parent:
                        riga = riga.parent
                        if "Risultato:" in riga.get_text(): break
                m_ris = re.search(r'Risultato:\s*(\d+)\s*-\s*(\d+)', riga.get_text())
                if m_ris:
                    print(f"   [{i}/{len(links)}] Apertura Referto G{giornata}...")
                    processa_referto("https://referto.plvhitball.it/" + a_tag['href'].lstrip('/'), giornata, int(m_ris.group(1)), int(m_ris.group(2)), db, mappa, stato_db)
        
        except Exception as e:
            print(f">>> Errore: {e}")

if __name__ == "__main__":
    mappa_g = carica_anagrafica_locale()
    if mappa_g:
        db_fs = inizializza_firebase()
        stato_attuale = scarica_stato_firebase(db_fs)
        recupera_e_analizza(db_fs, mappa_g, stato_attuale)
    print("\n>>> TUTTO AGGIORNATO E AL SICURO.")
