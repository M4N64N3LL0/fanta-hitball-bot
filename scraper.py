import requests
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore

# 1. CONFIGURAZIONE FIREBASE
# Assicurati di avere il file .json della chiave privata scaricato da Firebase Console
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

def calcola_punti_giocatore(nome_reale, punti_fatti, categoria):
    """
    Calcola il punteggio secondo le regole del FantaHitball
    """
    # Esempio di calcolo basato sulla categoria
    moltiplicatore = 1.0
    if categoria == "A1": moltiplicatore = 1.0
    elif categoria == "A2": moltiplicatore = 1.2
    elif categoria == "B1": moltiplicatore = 1.5
    elif categoria == "B2": moltiplicatore = 2.0
    elif categoria == "FEM": moltiplicatore = 2.5
    
    punteggio_finale = punti_fatti * moltiplicatore
    return round(punteggio_finale, 2)

def scrap_referto_e_aggiorna(url_referto, giornata):
    print(f"Inizio scansione referto: {url_referto}")
    response = requests.get(url_referto)
    soup = BeautifulSoup(response.text, 'html.parser')

    # Trova la tabella dei giocatori (la struttura dipende dal sito PLV)
    # Cerchiamo le righe dei giocatori nelle tabelle delle due squadre
    tabelle_squadre = soup.find_all('table', class_='table-condensed')

    for tabella in tabelle_squadre:
        righe = tabella.find_all('tr')[1:]  # Salta l'intestazione
        for riga in righe:
            colonne = riga.find_all('td')
            if len(colonne) < 3: continue
            
            nome_reale = colonne[1].text.strip().upper()
            try:
                punti_fatti = int(colonne[2].text.strip())
            except:
                punti_fatti = 0

            if nome_reale:
                # Recupera info giocatore dal DB per conoscere la categoria
                doc_ref = db.collection('giocatori').document(nome_reale)
                doc = doc_ref.get()
                
                if doc.exists:
                    dati = doc.to_dict()
                    categoria = dati.get('categoria', 'A1')
                    
                    # Esegue il calcolo dei punti
                    punti_fanta = calcola_punti_giocatore(nome_reale, punti_fatti, categoria)
                    
                    # Aggiorna Firebase: Punti per la giornata specifica
                    doc_ref.set({
                        'punti_giornate': {
                            str(giornata): punti_fanta
                        }
                    }, merge=True)
                    
                    print(f"Aggiornato {nome_reale}: {punti_fanta} punti (Giornata {giornata})")
                else:
                    print(f"Giocatore {nome_reale} non trovato nel database per il calcolo categoria.")

# --- ESECUZIONE ---
# Inserisci qui l'URL del referto della partita giocata
url_esempio = "https://referto.plvhitball.it/index.php?route=referto/referto&referto_id=XXXX"
giornata_da_aggiornare = 1

scrap_referto_e_aggiorna(url_esempio, giornata_da_aggiornare)
print("Operazione completata.")
