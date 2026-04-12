import firebase_admin
from firebase_admin import credentials, firestore
import json
import os

def ripristina_punti_blindato():
    if not firebase_admin._apps:
        cred = credentials.Certificate("chiave.json")
        firebase_admin.initialize_app(cred)
    
    db = firestore.client()

    with open("database_giocatori.json", "r", encoding="utf-8") as f:
        dati_json = json.load(f)

    docs = db.collection("giocatori").stream()
    firebase_map = {doc.id.strip().upper(): doc.id for doc in docs}

    batch = db.batch()
    contatore = 0

    for g in dati_json:
        nome_json = g.get("nome_reale", "").strip().upper()

        if nome_json in firebase_map:
            doc_id = firebase_map[nome_json]
            doc_ref = db.collection("giocatori").document(doc_id)
            
            # Recuperiamo i punti dal JSON se esistono
            punti_raw = g.get("punti_giornate", {})
            
            # FORZATURA: Trasformiamo tutto in una mappa pulita per Firebase
            # Chiave deve essere Stringa ("1"), Valore deve essere Numero (5.0)
            punti_puliti = {}
            if isinstance(punti_raw, dict):
                for k, v in punti_raw.items():
                    punti_puliti[str(k)] = float(v) if v is not None else 0.0
            elif isinstance(punti_raw, list):
                # Se per errore nel JSON è una lista, la convertiamo in mappa
                for i, v in enumerate(punti_raw):
                    punti_puliti[str(i+1)] = float(v) if v is not None else 0.0

            update_data = {
                "prezzo": g.get("prezzo", 0),
                "categoria": g.get("categoria", "A1"),
                "punti_giornate": punti_puliti # Sovrascrive la vecchia mappa errata
            }

            # Usiamo set con merge=True per aggiornare solo questi campi
            batch.set(doc_ref, update_data, merge=True)
            contatore += 1

            if contatore % 400 == 0:
                batch.commit()
                batch = db.batch()
                print(f"Sincronizzati {contatore} giocatori...")

    batch.commit()
    print(f"\nSISTEMATO! Controlla ora su Firebase: 'punti_giornate' deve essere una MAP.")

if __name__ == "__main__":
    ripristina_punti_blindato()
