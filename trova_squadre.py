import json
import requests
import re
from bs4 import BeautifulSoup

ID_CAMPIONATI = [39, 41, 42, 43] 
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

print(">>> AVVIO BOT CERCA-SQUADRE...")

# 1. Carichiamo il file JSON attuale 
try:
    with open("giocatori.json", "r", encoding="utf-8") as f:
        giocatori = json.load(f)
    print(f">>> Trovati {len(giocatori)} giocatori nel file JSON.")
except Exception as e:
    print(f"ERRORE: Impossibile leggere giocatori.json -> {e}")
    exit()

mappa_indici = {}
for i, g in enumerate(giocatori):
    nome_originale = g['nome_reale'].upper()
    nome_clean = re.sub(r'[^A-ZÀÈÉÌÒÙÁÍÓÚ\'\s]', ' ', nome_originale).strip()
    parole = frozenset(nome_clean.split())
    mappa_indici[parole] = i
    
    if 'squadra' not in giocatori[i] or "PLV" in giocatori[i].get('squadra', '').upper():
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
                
                res_ref = requests.get(url_referto, headers=HEADERS, timeout=10)
                soup_ref = BeautifulSoup(res_ref.text, 'html.parser')
                testo_pagina = soup_ref.get_text(separator=' ')
                
                if "tavolino" in testo_pagina.lower():
                    continue
                
                liste_squadre = soup_ref.find_all('ul', class_=re.compile(r'list-group', re.I))
                if len(liste_squadre) < 2:
                    continue
                    
                # IL FIX È QUI: Usiamo H1 invece di Title per evitare il nome del sito!
                intestazione = soup_ref.find('h1')
                if not intestazione:
                    continue
                    
                titolo = intestazione.get_text(strip=True).upper()
                squadre_raw = re.split(r'\s+-\s+|\s+VS\s+', titolo)
                
                if len(squadre_raw) < 2:
                    continue
                
                sq_casa = squadre_raw[0].strip()
                sq_trasf = squadre_raw[1].strip()

                def assegna_squadra(ul_node, nome_squadra):
                    global giocatori_aggiornati
                    for li in ul_node.find_all('li', class_=re.compile(r'list-group-item', re.I)):
                        testo = li.get_text(separator=' ', strip=True).upper()
                        testo_upper = testo.replace('’', "'").replace('‘', "'").replace('`', "'")
                        testo_clean = re.sub(r'[^A-ZÀÈÉÌÒÙÁÍÓÚ\'\s]', ' ', testo_upper)
                        parole_web = testo_clean.split()
                        
                        for parole_json, indice in mappa_indici.items():
                            if all(p in parole_web for p in parole_json):
                                if not giocatori[indice]['squadra']:
                                    giocatori[indice]['squadra'] = nome_squadra
                                    giocatori_aggiornati += 1
                                    print(f"   [+] {giocatori[indice]['nome_reale']} gioca nel {nome_squadra}")
                                break

                assegna_squadra(liste_squadre[0], sq_casa)
                assegna_squadra(liste_squadre[1], sq_trasf)

    except Exception as e:
        print(f"Errore: {e}")

# 4. Salvataggio del nuovo file
nome_nuovo_file = "giocatori_con_squadre.json"
with open(nome_nuovo_file, "w", encoding="utf-8") as f:
    json.dump(giocatori, f, indent=4, ensure_ascii=False)

print(f"\n>>> FINITO! Squadre assegnate a {giocatori_aggiornati} giocatori.")
