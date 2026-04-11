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
        print(f"-> Analisi Campionato: {cat_nome}")
        try:
            r = requests.get(url)
            soup = BeautifulSoup(r.text, 'html.parser')
        except:
            continue
        
        # 1. Trova TUTTI i link ai referti della pagina (ignorando la struttura HTML)
        links = set()
        for a in soup.find_all('a', href=True):
            if 'route=match/result' in a['href']:
                links.add(urllib.parse.urljoin("https://referto.plvhitball.it/", a['href']))
        
        print(f"Trovate {len(links)} partite da analizzare.")
        
        for l in links:
            try:
                rp = requests.get(l)
                sp = BeautifulSoup(rp.text, 'html.parser')
                testo_pagina = sp.get_text(separator=" ")
                
                # Trova la giornata
                match_g = re.search(r'Giornata[^\d]*(\d+)', testo_pagina, re.I)
                g_id = match_g.group(1) if match_g else "1"
                
                # Trova tutte le righe HTML che contengono la scritta "x2" (i giocatori)
                tags = sp.find_all(string=re.compile(r'x\s*2', re.I))
                rows = []
                for t in tags:
                    p = t.find_parent(['li', 'tr', 'div'])
                    if p and p not in rows:
                        rows.append(p)
                
                if not rows: continue
                
                # Le due squadre sono divise a metà nella pagina
                half = len(rows) // 2
                teamA_rows = rows[:half]
                teamB_rows = rows[half:]
                
                def processa_team(team_rows):
                    p_h = 0
                    ah_s = 0
                    dati = []
                    for r in team_rows:
                        testo = r.get_text(separator=" ", strip=True).upper()
                        
                        # Estrazione Nome super-sicura: prende le lettere fino al primo numero o "Tot"
                        nome_parts = []
                        for s in r.stripped_strings:
                            if re.match(r'^\d+$', s) or s.lower() in ['x2', 'x3', 'x', 'autohit', 'tot.', 'tot']:
                                break
                            if re.match(r'[A-Za-z]', s):
                                nome_parts.append(s)
                        nome = " ".join(nome_parts).strip()
                        if not nome: continue
                        
                        # Regex per i punti (gestisce spazi e puntini)
                        h2 = sum(int(x) for x in re.findall(r'(\d+)\s*\.?\s*X\s*2', testo))
                        h3 = sum(int(x) for x in re.findall(r'(\d+)\s*\.?\s*X\s*3', testo))
                        ah = sum(int(x) for x in re.findall(r'(\d+)\s*\.?\s*X\s*AUTOHIT', testo))
                        
                        amm = len(r.find_all('i', class_=re.compile(r'warning')))
                        esp = len(r.find_all('i', class_=re.compile(r'danger')))
                        
                        p_h += (h2 * 2) + (h3 * 3)
                        ah_s += ah
                        dati.append({'nome': nome, 'h2': h2, 'h3': h3, 'ah': ah, 'amm': amm, 'esp': esp})
                    return p_h, ah_s, dati

                phA, ahA, gA = processa_team(teamA_rows)
                phB, ahB, gB = processa_team(teamB_rows)
                
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
                print(f"Errore analisi partita {l}: {e}")
                continue

    print("\nInizio Salvataggio su Firebase...")
    batch = db.batch()
    
    # SOVRASCRITTURA COMPLETA (Niente merge=True)
    for n, d in giocatori_db.items():
        d["punteggio_campionato"] = sum(d["punti_giornate"].values())
        d["prezzo"] = max(10, int(d["punteggio_campionato"] * 2))
        d["nome_reale"] = n
        d["nome_visualizzato"] = n
        batch.set(db.collection("giocatori").document(n), d) # Cancella i vecchi errori
        
    batch.commit()
    print("AGGIORNAMENTO FIREBASE COMPLETATO CON SUCCESSO!")

if __name__ == "__main__": 
    scraper_professionale()
