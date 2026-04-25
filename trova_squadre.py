import json
import requests
import re
from bs4 import BeautifulSoup

ID_CAMPIONATI = [39, 41, 42, 43] 
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

print(">>> AVVIO BOT CERCA-SQUADRE...")

# 1. Carichiamo il file JSON attuale (Senza squadre)
try:
    with open("giocatori.json", "r", encoding="utf-8") as f:
        giocatori = json.load(f)
    print(f">>> Trovati {len(giocatori)} giocatori nel file JSON.")
except Exception as e:
    print(f"ERRORE: Impossibile leggere giocatori.json -> {e}")
    exit()

# 2. Creiamo una mappa per trovare subito la posizione (indice) del giocatore nel JSON
# Usiamo il filtro perfetto (con accenti e apostrofi) che abbiamo creato prima!
mappa_indici = {}
for i, g in enumerate(giocatori):
    nome_originale = g['nome_reale'].upper()
    nome_clean = re.sub(r'[^A-ZÀÈÉÌÒÙÁÍÓÚ\'\s]', ' ', nome_originale).strip()
    parole = frozenset(nome_clean.split())
    mappa_indici[parole] = i
    
    # Inizializziamo il campo "squadra" vuoto se non esiste
    if 'squadra' not in giocatori[i]:
        giocatori[i]['squadra'] = ""

giocatori_aggiornati = 0

# 3. Inizia la scansione dei referti
for camp_id in ID_CAMPIONATI:
    url_camp = f"https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id={camp_id}"
    print(f"\n>>> Scansione Campionato {camp_id}...")
    
    try:
        res = requests.get(url_camp, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        for a_tag in soup.find_all('a', href=True):
            if 'match_id=' in a_tag['href'] or 'referto_id=' in a_tag['href']:
                url_referto = "https://referto.plvhitball.it/" + a_tag['href'].lstrip('/')
                
                # Apriamo il referto
                res_ref = requests.get(url_referto, headers=HEADERS, timeout=10)
                soup_ref = BeautifulSoup(res_ref.text, 'html.parser')
                testo_pagina = soup_ref.get_text(separator=' ')
                
                # Saltiamo i tavolini perché le liste giocatori sono vuote o finte
                if "tavolino" in testo_pagina.lower():
                    continue
                
                liste_squadre = soup_ref.find_all('ul', class_=re.compile(r'list-group', re.I))
                if len(liste_squadre) < 2:
                    continue
                    
                # Estraiamo i nomi delle due squadre dal titolo della pagina
                titolo = soup_ref.find('title').get_text(strip=True).upper() if soup_ref.find('title') else ""
                squadre_raw = re.split(r'\s+-\s+|\s+VS\s+', titolo)
                
                if len(squadre_raw) < 2:
                    continue
                
                # Puliamo i nomi delle squadre
                sq_casa = re.sub(r'\s*REFERTO.*', '', squadre_raw[0]).strip()
                sq_trasf = re.sub(r'\s*REFERTO.*', '', squadre_raw[1]).strip()

                # Funzione interna per abbinare i giocatori alla squadra corretta
                def assegna_squadra(ul_node, nome_squadra):
                    global giocatori_aggiornati
                    for li in ul_node.find_all('li', class_=re.compile(r'list-group-item', re.I)):
                        testo = li.get_text(separator=' ', strip=True).upper()
                        testo_upper = testo.replace('’', "'").replace('‘', "'").replace('`', "'")
                        testo_clean = re.sub(r'[^A-ZÀÈÉÌÒÙÁÍÓÚ\'\s]', ' ', testo_upper)
                        parole_web = testo_clean.split()
                        
                        for parole_json, indice in mappa_indici.items():
                            if all(p in parole_web for p in parole_json):
                                # Se il giocatore non ha ancora la squadra assegnata, gliela diamo
                                if not giocatori[indice]['squadra']:
                                    giocatori[indice]['squadra'] = nome_squadra
                                    giocatori_aggiornati += 1
                                    print(f"   [+] {giocatori[indice]['nome_reale']} gioca nel {nome_squadra}")
                                break

                # Assegniamo la squadra di Casa (lista 0) e Trasferta (lista 1)
                assegna_squadra(liste_squadre[0], sq_casa)
                assegna_squadra(liste_squadre[1], sq_trasf)

    except Exception as e:
        print(f"Errore durante l'analisi: {e}")

# 4. Salvataggio del nuovo file
nome_nuovo_file = "giocatori_con_squadre.json"
with open(nome_nuovo_file, "w", encoding="utf-8") as f:
    json.dump(giocatori, f, indent=4, ensure_ascii=False)

print(f"\n>>> FINITO! Squadre assegnate a {giocatori_aggiornati} giocatori.")
print(f">>> È stato creato il file '{nome_nuovo_file}'.")
print(">>> Rinomina questo file in 'giocatori.json' e usalo per il bot principale!")