"""Cria um banco novo: vazio ou com cenário inteiramente fictício."""
import argparse
from calendar import monthrange
from datetime import date
from pathlib import Path
import re

from fastapi.testclient import TestClient
from sqlalchemy import inspect
from app.main import create_app
from app.persistence.database import make_engine
from app.persistence.migrations import upgrade_database


def prepare(mode, destination, reference=None):
    destination = Path(destination).resolve()
    # Nunca preenche, limpa, migra ou substitui um arquivo preexistente.
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb'):
        pass
    upgrade_database(destination)
    if mode == 'empty':
        engine = make_engine(destination)
        try:
            with engine.connect() as connection:
                for table in inspect(connection).get_table_names():
                    if table != 'alembic_version':
                        assert connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{table}"').scalar() == 0
        finally:
            engine.dispose()
        return
    today = date.today()
    anchor = date.fromisoformat((reference or today.strftime('%Y-%m')) + '-01')
    def day(number):
        return anchor.replace(day=min(number, monthrange(anchor.year, anchor.month)[1])).isoformat()
    with TestClient(create_app(destination), base_url='http://127.0.0.1:8000') as client:
        page = client.get('/')
        token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
        client.headers.update({'Origin':'http://127.0.0.1:8000', 'X-CSRF-Token':token})
        def post(path, payload):
            response = client.post('/api/' + path, json=payload)
            if response.status_code not in (200, 201):
                raise RuntimeError(f'{path}: {response.status_code}: {response.text}')
            return response.json()
        catalogs = post('setup', {'effective_from':day(1)})
        people = {row['code']:row['id'] for row in catalogs['responsibles']}
        cards = {row['name']:row['id'] for row in catalogs['cards']}
        categories = {name:post('categories', {'name':name})['id'] for name in
                      ['Alimentação fictícia','Estudos fictícios','Transporte fictício','Receitas fictícias','Moradia fictícia']}
        # Valores inventados para ensinar lançamentos, parcelas e previsão.
        samples = [
            ('Bolsa de estudos fictícia', 280000, 'income', 'pix', None, 1, 'self', 'Receitas fictícias', 1),
            ('Mercado de exemplo', 18640, 'expense', 'credit', 'Mercado Pago/Livre', 1, 'self', 'Alimentação fictícia', 2),
            ('Curso de exemplo', 72000, 'expense', 'credit', 'Nubank', 3, 'self', 'Estudos fictícios', 3),
            ('Transporte de exemplo', 1850, 'expense', 'pix', None, 1, 'self', 'Transporte fictício', 4),
            ('Compra familiar de exemplo', 36000, 'expense', 'credit', 'Nubank', 2, 'parents', 'Moradia fictícia', 6),
        ]
        for index, (description, total, kind, method, card, parts, person, category, number) in enumerate(samples):
            post('entries', dict(request_key=f'demo-entry-{index}', description=description,
                 occurred_on=day(number), total_cents=total, kind=kind, payment_method=method,
                 installment_count=parts, responsible_id=people[person], card_id=cards.get(card),
                 category_id=categories[category]))
        for index, (description, amount, kind, method, card, category, number) in enumerate([
            ('Internet fictícia',9900,'expense','credit','Nubank','Moradia fictícia',8),
            ('Bolsa mensal prevista fictícia',280000,'income','pix',None,'Receitas fictícias',1),
            ('Aluguel fictício',85000,'expense','pix',None,'Moradia fictícia',10),
        ]):
            start = (date(anchor.year + (anchor.month == 12), anchor.month % 12 + 1, 1).isoformat()
                     if kind == 'income' else day(number))
            post('recurrences', dict(request_key=f'demo-series-{index}',reason='Cenário didático fictício',
                 description=description,amount_cents=amount,kind=kind,payment_method=method,
                 card_id=cards.get(card),responsible_id=people['self'],category_id=categories[category],start_on=start))
        result = client.get('/api/dashboard', params={'month':anchor.strftime('%Y-%m'),'months':3})
        assert result.status_code == 200, result.text
        assert client.get('/api/entries').json()['total'] == len(samples)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['empty','demo'])
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--month',help='Mês do exemplo em AAAA-MM; padrão: mês local atual.')
    args=parser.parse_args()
    if args.month:
        date.fromisoformat(args.month+'-01')
    prepare(args.mode,args.database,args.month)
    print(f'Banco {args.mode} criado: {args.database}')
    if args.mode == 'demo': print('Exemplos totalmente fictícios. Consulte o mês escolhido na Visão geral.')


if __name__ == '__main__':
    main()
