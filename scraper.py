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
    if firebase_secret:
        cred_dict = json.loads(firebase_secret)
        cred = credentials.Certificate(cred_dict)
    else:
        cred = credentials.Certificate("chiave.json")
    firebase_admin.initialize_app(cred)
    return firestore.client()

def calcola_bonus_squadra(p_fatti, p_subiti):
    bonus = 0.0
    if 51 <= p_fatti <= 75: bonus += 2.0
    elif p_fatti >= 76: bonus += 3.0
    if p_subiti <= 50: bonus += 5.0
    elif 51 <= p_subiti <= 75: bonus += 2.0
    elif p_subiti >= 101: bonus -= 5.0
    if p_fatti > p_subiti: bonus += 2.0
    elif p_fatti == p_subiti: bonus += 1.0
    return bonus

def scraper_professionale():
    db = avvia_firebase()
    if not db: return 

    campionati = {"A1": "39", "A2": "41", "B1": "42", "B2": "43", "FEM": "47"}
    lista_femm = ["Federica Funnone", "Martina Lupo", "Sabrina Capitolo", "Arianna Vismara", "Sabrina Zanfretta", "Sara Sottolano", "Martina Bracesco", "Rossella De Blasio", "Carlotta Amodeo", "Federica Amorelli", "Elena Pasino", "Mara Ferraris", "Alice La Versa", "Noemi Castelluccio", "Chiara Gilardi"]
    giocatori_db = {}
    
    for cat_nome, c_id in campionati.items():
        url = f"https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id={c_id}"
        print(f"\n-> Analisi Campionato: {cat_nome}")
        try:
            r = requests.get(url)
            soup = BeautifulSoup(r.text, 'html.parser')
        except: continue
        
        mappa_partite = {}
        
        # 1. MAPPATURA PERFETTA BASATA SULLO SCREENSHOT
        for a in soup.find_all('a', href=True):
            if 'route=match/result' in a['href']:
                link = urllib.parse.urljoin("https://referto.plvhitball.it/", a['href'])
                
                # Cerca il testo andando indietro dal link, fregandosene dei tag HTML
                # Cerca "Giornata X" oppure "X Giornata"
                nodo_testo = a.find_previous(string=re.compile(r'giornata\s*\d+|\d+[^\w]*giornata', re.I))
                g_id = "1" # Sicurezza base
                
                if nodo_testo:
                    testo = str(nodo_testo).lower()
                    match_g = re.search(r'giornata\s*(\d+)|(\d+)[^\w]*giornata', testo)
                    if match_g:
                        g_id = match_g.group(1) or match_g.group(2)
                
                mappa_partite[link] = g_id

        print(f"Mappate {len(mappa_partite)} partite.")
        
        # 2. LETTURA REFERTI
        for l, g_id in mappa_partite.items():
            try:
                rp = requests.get(l)
                sp = BeautifulSoup(rp.text, 'html.parser')
                
                valid_nodes = []
                for node in sp.find_all(['li', 'tr']):
                    for i_tag in node.find_all(['i', 'span']):
                        c = str(i_tag.get('class', [])).lower()
                        if 'times' in c or 'close' in c: i_tag.replace_with(" X ")
                    testo_nodo = node.get_text(separator=" ", strip=True).upper().replace('×', 'X').replace('*', 'X')
                    if re.search(r'X\s*2|X\s*3|AUTOHIT', testo_nodo):
                        valid_nodes.append(node)
                
                if not valid_nodes: continue
                
                containers = []
                for node in valid_nodes:
                    parent = node.find_parent(['ul', 'tbody', 'table'])
                    if parent and parent not in containers:
                        containers.append(parent)
                
                if len(containers) < 2: continue
                
                def processa_team(parent_node):
                    p_h, ah_s = 0, 0
                    dati = []
                    for n in parent_node.find_all(['li', 'tr']):
                        testo = n.get_text(separator=" ", strip=True).upper().replace('×', 'X').replace('*', 'X')
                        if not re.search(r'\d', testo): continue
                        
                        nome = list(n.stripped_strings)[0] if list(n.stripped_strings) else ""
                        if not nome or len(nome) < 3 or "TOTALE" in nome: continue
                        
                        h2 = sum(int(x) for x in re.findall(r'(\d+)\s*X\s*2', testo))
                        h3 = sum(int(x) for x in re.findall(r'(\d+)\s*X\s*3', testo))
                        ah = sum(int(x) for x in re.findall(r'(\d+)\s*AUTOHIT', testo))
                        amm = len(n.find_all(['i', 'span'], class_=re.compile(r'warning', re.I)))
                        esp = len(n.find_all(['i', 'span'], class_=re.compile(r'danger', re.I)))
                        
                        p_h += (h2 * 2) + (h3 * 3)
                        ah_s += ah
                        dati.append({'nome': nome, 'h2': h2, 'h3': h3, 'ah': ah, 'amm': amm, 'esp': esp})
                    return p_h, ah_s, dati

                phA, ahA, gA = processa_team(containers[0])
                phB, ahB, gB = processa_team(containers[1])
                
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
                        
                        if str(g_id) in giocatori_db[g['nome']]["punti_giornate"]:
                            giocatori_db[g['nome']]["punti_giornate"][str(g_id)] += p_tot
                        else:
                            giocatori_db[g['nome']]["punti_giornate"][str(g_id)] = p_tot
            except Exception as e: continue

    print("\nSalvataggio su Firebase in corso...")
    batch = db.batch()
    count = 0
    
    for n, d in giocatori_db.items():
        d["punteggio_campionato"] = sum(d["punti_giornate"].values())
        d["prezzo"] = max(10, int(d["punteggio_campionato"] * 2))
        d["nome_reale"] = n
        d["nome_visualizzato"] = n
        batch.set(db.collection("giocatori").document(n), d)
        
        count += 1
        if count == 450:
            batch.commit()
            batch = db.batch()
            count = 0
            
    if count > 0:
        batch.commit()
    print(f"FINITO: Salvati {len(giocatori_db)} giocatori con Successo!")

if __name__ == "__main__": 
    scraper_professionale()
