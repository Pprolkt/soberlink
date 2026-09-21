import psycopg2

def get_connection():
    return psycopg2.connect(
        host="localhost",
        dbname="soberlink",
        user="postgres",
        password="soberlink_dev",
        port=5432,
    )
