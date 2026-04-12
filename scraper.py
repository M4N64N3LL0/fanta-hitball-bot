import requests
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore
import urllib.parse
import os
import json

def avvia_firebase():
    """Inizializza Firebase in modo sicuro (Locale o Server)"""
    if firebase_admin._apps:
        return firestore.client()
        
    firebase_secret = os.environ.get("FIREBASE_KEY")
    
    try:
        if firebase_secret:
            cred_dict = json.loads(firebase_secret)
            cred = credentials.Certificate(cred_dict)
            print("Avvio in modalità SERVER (GitHub Actions) - OK")
        else:
            cred = credentials.Certificate("chiave.json")
            print("Avvio in modalità LOCALE (VS Code) - OK")
            
        firebase_admin.initialize_app(cred)
        return firestore.client()
    except Exception as e:
        print(f"ERRORE CRITICO nell'avvio di Firebase: {e}")
        return None

def calcola_bonus_squadra(punti_fatti, punti_subiti):
    bonus = 0.0
    # Prestazione Offensiva
    if 51 <= punti_fatti <= 75:
        bonus += 2.0
    elif punti_fatti >= 76:
        bonus += 3.0
        
    # Prestazione Difensiva
    if punti_subiti <= 50:
        bonus += 5.0
    elif 51 <= punti_subiti <= 75:
        bonus += 2.0
    elif punti_subiti >= 101:
        bonus -= 5.0
        
    return bonus

def estrai_dati_squadra(team_list):
    h2_tot, h3_tot, ah_tot = 0, 0, 0
    giocatori_html = team_list.find_all('li', class_='list-group-item')
    
    for blocco in giocatori_html:
        testi = list(blocco.stripped_strings)
        h2_tot += sum([int(t.replace('x2','').strip() or 0) for t in testi if 'x2' in t])
        h3_tot += sum([int(t.replace('x3','').strip() or 0) for t in testi if 'x3' in t])
        ah_tot += sum([int(t.replace('AUTOHIT','').replace('x','').strip() or 0) for t in testi if 'AUTOHIT' in t])
        
    punti_reali_fatti = (h2_tot * 2) + (h3_tot * 3)
    return punti_reali_fatti, ah_tot, giocatori_html

# --- NUOVA FUNZIONE: RECUPERA LE FOTO DALLA PAGINA GIOCATORI ---
def estrai_catalogo_foto():
    print("Scansione pagina giocatori per estrarre il database fotografico...")
    catalogo = {}
    
    # Scansioniamo fino a 5 pagine nel caso in cui i giocatori siano divisi in più pagine
    for page in range(1, 6):
        url = f"https://referto.plvhitball.it/index.php?route=team/player&page={page}"
        r = requests.get(url)
        if r.status_code != 200: break
            
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # Troviamo tutte le immagini della pagina
        immagini = soup.find_all('img')
        for img in immagini:
            src = img.get('src', '')
            if not src or "placeholder" in src.lower() or "no_image" in src.lower():
                continue
                
            # Costruiamo il link assoluto
            foto_url = src if src.startswith('http') else f"https://referto.plvhitball.it/{src.lstrip('/')}"
            
            # Recuperiamo il nome del giocatore (solitamente nell'alt, title o nel testo vicino)
            nome = img.get('alt', '').strip().upper()
            if not nome or nome == 'IMAGE':
                nome = img.get('title', '').strip().upper()
                
            if not nome:
                # Se non ha alt, proviamo a prendere il testo subito sotto l'immagine
                parent = img.find_parent('div')
                if parent:
                    testi = [t.strip().upper() for t in parent.stripped_strings if len(t) > 3 and not t.lower().endswith(('.png', '.jpg'))]
                    if testi: nome = testi[0]
            
            if nome:
                catalogo[nome] = foto_url
                
    print(f"Fatto! Trovate {len(catalogo)} foto nel catalogo PLV.")
    return catalogo

def scraper_professionale():
    db = avvia_firebase()
    if not db:
        return 

    # SCARICHIAMO LE FOTO PRIMA DI INIZIARE CON I REFERTI
    catalogo_foto = estrai_catalogo_foto()

    campionati = {
        "A1": "https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id=39",
        "A2": "https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id=41",
        "B1": "https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id=42",
        "B2": "https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id=43",
        "FEMMINILE": "https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id=47"
    }

    lista_femm = ["Federica Funnone", "Martina Lupo", "Sabrina Capitolo", "Arianna Vismara", "Sabrina Zanfretta", "Sara Sottolano", "Martina Bracesco", "Rossella De Blasio", "Carlotta Amodeo", "Federica Amorelli", "Elena Pasino", "Mara Ferraris", "Alice La Versa", "Noemi Castelluccio", "Chiara Gilardi"]

    giocatori_data = {}
    print("=== AVVIO MEGA-BOT (REGOLAMENTO COMPLETO + FOTO) ===")

    for cat_nome, url in campionati.items():
        print(f"Esplorando {cat_nome}...")
        r = requests.get(url)
        soup = BeautifulSoup(r.text, 'html.parser')
        links = list(set([urllib.parse.urljoin("https://referto.plvhitball.it/", a['href']) for a in soup.find_all('a', href=True) if 'route=match/result' in a['href']]))
        
        for l in links:
            giornata_id = "1" 
            
            rp = requests.get(l)
            sp = BeautifulSoup(rp.text, 'html.parser')

            team_lists = sp.find_all('ul', class_='list-group')
            
            if len(team_lists) >= 2:
                # SQUADRA A
                punti_A_hits, ah_A, giocatori_A = estrai_dati_squadra(team_lists[0])
                # SQUADRA B
                punti_B_hits, ah_B, giocatori_B = estrai_dati_squadra(team_lists[1])
                
                score_A = punti_A_hits + ah_B
                score_B = punti_B_hits + ah_A
                
                # --- LOGICA SCONFITTA A TAVOLINO INTELLIGENTE ---
                tavolino_A = 0.0
                tavolino_B = 0.0
                if "tavolino" in sp.text.lower():
                    if punti_A_hits == 0: tavolino_A = -20.0
                    if punti_B_hits == 0: tavolino_B = -20.0

                bonus_squadra_A = calcola_bonus_squadra(score_A, score_B)
                bonus_squadra_B = calcola_bonus_squadra(score_B, score_A)
                
                squadre_da_processare = [
                    (giocatori_A, bonus_squadra_A, tavolino_A), 
                    (giocatori_B, bonus_squadra_B, tavolino_B)
                ]
                
                for giocatori_html, bonus_team, malus_tavolino in squadre_da_processare:
                    for blocco in giocatori_html:
                        testi = list(blocco.stripped_strings)
                        if not testi: continue
                        nome = testi[0]
                        
                        # --- NUOVA SEZIONE: ASSEGNAZIONE FOTO DAL CATALOGO ---
                        nome_pulito = nome.strip().upper()
                        foto_url = catalogo_foto.get(nome_pulito)
                        
                        # Se il nome sul referto è leggermente diverso, facciamo un match flessibile
                        if not foto_url:
                            for k, v in catalogo_foto.items():
                                if nome_pulito in k or k in nome_pulito:
                                    foto_url = v
                                    break
                        # -----------------------------------------------------
                        
                        h2 = sum([int(t.replace('x2','').strip() or 0) for t in testi if 'x2' in t])
                        h3 = sum([int(t.replace('x3','').strip() or 0) for t in testi if 'x3' in t])
                        ah = sum([int(t.replace('AUTOHIT','').replace('x','').strip() or 0) for t in testi if 'AUTOHIT' in t])
                        amm = len(blocco.find_all('i', class_='text-warning'))
                        esp = len(blocco.find_all('i', class_='text-danger'))
                        
                        moltiplicatore_hit = 2.0 if nome in lista_femm else 1.0
                        
                        # CALCOLO FINALE DEL SINGOLO GIOCATORE
                        punti_base = ((h2 + h3) * moltiplicatore_hit) - ah - (amm * 10) - (esp * 20)
                        punti_totali = punti_base + bonus_team + malus_tavolino

                        if nome not in giocatori_data:
                            giocatori_data[nome] = {
                                "punti_giornate": {},
                                "nome_reale": nome
                            }
                            if foto_url:
                                giocatori_data[nome]["foto_url"] = foto_url
                        elif foto_url and "foto_url" not in giocatori_data[nome]:
                            giocatori_data[nome]["foto_url"] = foto_url
                        
                        giocatori_data[nome]["punti_giornate"][giornata_id] = punti_totali

    print("\nSincronizzazione Cloud in corso...")
    batch = db.batch()
    contatore = 0
    for nome, info in giocatori_data.items():
        doc_ref = db.collection("giocatori").document(nome)
        batch.set(doc_ref, info, merge=True)
        contatore += 1
        if contatore % 450 == 0:
            batch.commit()
            batch = db.batch()
            
    batch.commit()
    print("=== DATABASE AGGIORNATO CON SUCCESSO! ===")

if __name__ == "__main__":
    scraper_professionale()
