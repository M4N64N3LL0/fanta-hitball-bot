import requests
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore
import urllib.parse
import os
import json
import re

# ==========================================
# CONFIGURAZIONE E LISTA QUOTE ROSA
# ==========================================
QUOTE_ROSA = {
    "FEDERICA FUNNONE": "A1", 
    "MARTINA LUPO": "A1",
    "SABRINA CAPITOLO": "A2",
    "ARIANNA VISMARA": "B1", 
    "SABRINA ZANFRETTA": "B1", 
    "SARA SOTTOLANO": "B1", 
    "MARTINA BRACESCO": "B1", 
    "ROSSELLA DE BLASIO": "B1",
    "CARLOTTA AMODEO": "B2", 
    "FEDERICA AMORELLI": "B2", 
    "ELENA PASINO": "B2", 
    "MARA FERRARIS": "B2", 
    "ALICE LA VERSA": "B2", 
    "NOEMI CASTELLUCCIO": "B2", 
    "CHIARA GILARDI": "B2"
}

def avvia_firebase():
    if firebase_admin._apps: return firestore.client()
    firebase_secret = os.environ.get("FIREBASE_KEY")
    if firebase_secret:
        cred = credentials.Certificate(json.loads(firebase_secret))
    else:
        cred = credentials.Certificate("chiave.json")
    firebase_admin.initialize_app(cred)
    return firestore.client()

def calcola_bonus_squadra(p_fatti, p_subiti):
    bonus = 0.0
    # Bonus Punti Segnati
    if p_fatti >= 76: bonus += 5.0
    elif 51 <= p_fatti <= 75: bonus += 2.0
    
    # Bonus/Malus Punti Subiti
    if p_subiti <= 50: bonus += 5.0
    elif 51 <= p_subiti <= 75: bonus += 2.0
    elif p_subiti >= 101: bonus -= 5.0
    return bonus

def scraper_professionale():
    db = avvia_firebase()
    
    # 1. Caricamento White List dal file JSON (deve essere nella stessa cartella)
    try:
        with open("database_giocatori.json", "r", encoding="utf-8") as f:
            lista_ufficiale = json.load(f)
            nomi_ammessi = {g['nome_reale'].upper(): g for g in lista_ufficiale}
            print(f"White List caricata: {len(nomi_ammessi)} nomi ammessi.")
    except Exception as e:
        print(f"ERRORE caricamento database_giocatori.json: {e}")
        return

    # 2. Campionati da scansionare (ESCLUSO IL FEMMINILE ID 47)
    campionati = {"A1": "39", "A2": "41", "B1": "42", "B2": "43"}
    giocatori_db = {}
    
    for cat_nome, c_id in campionati.items():
        url = f"https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id={c_id}"
        print(f"\n--- Analisi Campionato: {cat_nome} ---")
        try:
            r = requests.get(url, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            
            mappa_partite = {}
            for a in soup.find_all('a', href=True):
                if 'route=match/result' in a['href']:
                    link = urllib.parse.urljoin("https://referto.plvhitball.it/", a['href'])
                    nodo_testo = a.find_previous(string=re.compile(r'giornata\s*\d+|\d+[^\w]*giornata', re.I))
                    g_id = str(re.search(r'\d+', str(nodo_testo)).group()) if nodo_testo else "1"
                    mappa_partite[link] = g_id

            for link_partita, giornata_id in mappa_partite.items():
                try:
                    rp = requests.get(link_partita, timeout=10)
                    sp = BeautifulSoup(rp.text, 'html.parser')
                    
                    # Individua tabelle referto
                    valid_nodes = []
                    for node in sp.find_all(['li', 'tr']):
                        for i_tag in node.find_all(['i', 'span']):
                            if 'times' in str(i_tag.get('class', [])).lower(): i_tag.replace_with(" X ")
                        testo = node.get_text(separator=" ", strip=True).upper().replace('×', 'X').replace('*', 'X')
                        if re.search(r'X\s*2|X\s*3|AUTOHIT', testo): valid_nodes.append(node)
                    
                    containers = []
                    for v in valid_nodes:
                        p = v.find_parent(['ul', 'tbody', 'table'])
                        if p and p not in containers: containers.append(p)
                    
                    if len(containers) < 2: continue

                    def processa_team(parent_node):
                        p_h, ah_s, dati = 0, 0, []
                        for n in parent_node.find_all(['li', 'tr']):
                            testo_raw = n.get_text(separator=" ", strip=True).upper().replace('×', 'X').replace('*', 'X')
                            if not re.search(r'\d', testo_raw): continue
                            strings = list(n.stripped_strings)
                            nome = strings[0] if strings else ""
                            if not nome or len(nome) < 3 or "TOTALE" in nome: continue
                            
                            # FILTRO WHITE LIST
                            if nome.upper() not in nomi_ammessi: continue
                            
                            h2 = sum(int(x) for x in re.findall(r'(\d+)\s*X\s*2', testo_raw))
                            h3 = sum(int(x) for x in re.findall(r'(\d+)\s*X\s*3', testo_raw))
                            ah = sum(int(x) for x in re.findall(r'(\d+)\s*AUTOHIT', testo_raw))
                            amm = len(n.find_all(['i', 'span'], class_=re.compile(r'warning', re.I)))
                            esp = len(n.find_all(['i', 'span'], class_=re.compile(r'danger', re.I)))
                            p_h += (h2 * 2) + (h3 * 3)
                            ah_s += ah
                            dati.append({'nome': nome, 'h2': h2, 'h3': h3, 'ah': ah, 'amm': amm, 'esp': esp})
                        return p_h, ah_s, dati

                    phA, ahA, gA = processa_team(containers[0])
                    phB, ahB, gB = processa_team(containers[1])
                    scA, scB = phA + ahB, phB + ahA
                    bonA, bonB = calcola_bonus_squadra(scA, scB), calcola_bonus_squadra(scB, scA)

                    for lista, bonus_squadra in [(gA, bonA), (gB, bonB)]:
                        for g in lista:
                            nome_u = g['nome'].upper()
                            is_fem = nome_u in QUOTE_ROSA
                            
                            # Forza categoria FEM per le ragazze, altrimenti usa quella della white list
                            categoria_fanta = "FEM" if is_fem else nomi_ammessi[nome_u]['categoria']
                            molt = 2.0 if is_fem else 1.0
                            
                            # Calcolo: (Hit * Molt) - Autohit - Malus Ammonizione/Espulsione + Bonus Squadra
                            p_tot = (((g['h2'] + g['h3']) * molt) - g['ah'] - (g['amm'] * 10) - (g['esp'] * 20)) + bonus_squadra
                            
                            if g['nome'] not in giocatori_db:
                                giocatori_db[g['nome']] = {"punti_giornate": {}, "categoria": categoria_fanta}
                            
                            # Somma algebrica per doppie partite nella stessa giornata
                            giocatori_db[g['nome']]["punti_giornate"][giornata_id] = giocatori_db[g['nome']]["punti_giornate"].get(giornata_id, 0) + p_tot
                except Exception as e:
                    print(f"Errore partita {link_partita}: {e}")
                    continue
        except Exception as e:
            print(f"Errore campionato {cat_nome}: {e}")

    # 3. Invio Batch a Firebase (Senza cancellare i prezzi)
    print(f"\nSalvataggio di {len(giocatori_db)} giocatori su Firebase...")
    batch = db.batch()
    for nome, info in giocatori_db.items():
        doc_ref = db.collection("giocatori").document(nome)
        aggiornamento = {
            "nome_reale": nome,
            "categoria": info["categoria"],
            "punti_giornate": info["punti_giornate"],
            "punteggio_campionato": sum(info["punti_giornate"].values())
        }
        batch.set(doc_ref, aggiornamento, merge=True)
    
    batch.commit()
    print("Operazione completata con successo.")

if __name__ == "__main__":
    scraper_professionale()
