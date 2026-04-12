import requests
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore
import os

# --- 1. CONFIGURAZIONE E ACCESSO (GITHUB O LOCALE) ---
firebase_secret = os.environ.get('FIREBASE_SERVICE_ACCOUNT')

if firebase_secret:
    with open("serviceAccountKey.json", "w") as f:
        f.write(firebase_secret)

cred = credentials.Certificate("serviceAccountKey.json")
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

# --- 2. LOGICA DEI MOLTIPLICATORI ---
# Questa mappa deve corrispondere a quella che abbiamo nel file Dart
QUOTE_ROSA_FEM = [
    "FEDERICA FUNNONE", "MARTINA LUPO", "SABRINA CAPITOLO", "ARIANNA VISMARA", 
    "SABRINA ZANFRETTA", "SARA SOTTOLANO", "MARTINA BRACESCO", "ROSSELLA DE BLASIO", 
    "CARLOTTA AMODEO", "FEDERICA AMORELLI", "ELENA PASINO", "MARA FERRARIS", 
    "ALICE LA VERSA", "NOEMI CASTELLUCCIO", "CHIARA GILARDI"
]

def calcola_punti_fanta(nome, punti_reali, categoria_db):
    nome_up = nome.upper().strip()
    
    # Determiniamo la categoria effettiva (controllo se è nelle quote rosa FEM)
    categoria = "FEM" if nome_up in QUOTE_ROSA_FEM else categoria_db
    
    # Moltiplicatori ufficiali
    moltiplicatori = {
        "A1": 1.0,
        "A2": 1.2,
        "B1": 1.5,
        "B2": 2.0,
        "FEM": 2.5
    }
    
    m = moltiplicatori.get(categoria, 1.0)
    return round(punti_reali * m, 2)

# --- 3. FUNZIONE PRINCIPALE DI SCRAPING ---
def aggiorna_giornata(url_referto, numero_giornata):
    print(f"Analizzando referto: {url_referto} per Giornata {numero_giornata}")
    
    try:
        res = requests.get(url_referto)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # Cerchiamo tutte le tabelle dei giocatori nel referto PLV
        # Di solito sono tabelle con classe 'table-striped' o simili
        tabelle = soup.find_all('table')
        
        giocatori_trovati = 0
        
        for tabella in tabelle:
            righe = tabella.find_all('tr')
            for riga in righe:
                colonne = riga.find_all('td')
                # Un riga valida di solito ha: Numero, Nome, Punti, Falli...
                if len(colonne) >= 3:
                    nome_raw = colonne[1].text.strip().upper()
                    try:
                        punti_reali = int(colonne[2].text.strip())
                    except ValueError:
                        continue # Non è una riga di un giocatore
                    
                    if nome_raw and punti_reali >= 0:
                        # 4. AGGIORNAMENTO SU FIREBASE
                        # Cerchiamo il giocatore nel DB per prendere la sua categoria
                        doc_ref = db.collection('giocatori').document(nome_raw)
                        doc = doc_ref.get()
                        
                        if doc.exists:
                            dati = doc.to_dict()
                            cat_db = dati.get('categoria', 'A1')
                            
                            punti_fanta = calcola_punti_fanta(nome_raw, punti_reali, cat_db)
                            
                            # Salviamo il punteggio nella mappa punti_giornate
                            doc_ref.set({
                                'punti_giornate': {
                                    str(numero_giornata): punti_fanta
                                }
                            }, merge=True)
                            
                            print(f"OK: {nome_raw} ({cat_db}) -> Reali: {punti_reali}, Fanta: {punti_fanta}")
                            giocatori_trovati += 1
        
        print(f"Fine. Aggiornati {giocatori_trovati} giocatori.")
        
    except Exception as e:
        print(f"Errore durante lo scraping: {e}")

# --- 4. ESECUZIONE ---
# Qui metterai l'URL della partita che vuoi scaricare
if __name__ == "__main__":
    # Esempio d'uso (puoi automatizzare questo con un ciclo o passandolo da GitHub)
    URL_PARTITA = "https://referto.plvhitball.it/index.php?route=referto/referto&referto_id=XXXX" 
    GIORNATA = 1 
    aggiorna_giornata(URL_PARTITA, GIORNATA)
