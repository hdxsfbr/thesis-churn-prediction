"""Measured source profile; no features, labels or PII values leave the source."""
from datetime import datetime
from pathlib import Path

import pandas as pd

from churn.synth.edge_cases import EVENT_TIMES, KEYS

VIEWS = ('subscriptions', 'orders', 'subscription_events', 'products', 'email_events',
         'support_tickets', 'quiz_responses', 'customers_pii')


def build_profile(wh, settings, scale='tiny'):
    schema = settings.schemas.synth
    rows = []

    def scalar(sql):
        return float(wh.query(sql).iloc[0, 0])

    def add(section, item, value):
        rows.append({'section': section, 'item': item, 'value': float(value)})

    n = scalar(f'SELECT COUNT(*) FROM {schema}.subscriptions')
    for name in VIEWS:
        add('rows', name, scalar(f'SELECT COUNT(*) FROM {schema}.{name}'))
    for item, table in [('no_quiz', 'quiz_responses'), ('no_email', 'email_events'),
                        ('no_tickets', 'support_tickets')]:
        add('edge_case', item, scalar(
            f'SELECT COUNT(*) FROM {schema}.subscriptions s WHERE NOT EXISTS '
            f'(SELECT 1 FROM {schema}.{table} t WHERE t.customer_id=s.customer_id)') / n)
    add('edge_case', 'single_order', scalar(
        f'SELECT COUNT(*) FROM (SELECT customer_id FROM {schema}.orders '
        'GROUP BY customer_id HAVING COUNT(DISTINCT order_id)=1)') / n)
    add('edge_case', 'new_subscriber', scalar(
        f"SELECT COUNT(*) FROM {schema}.subscriptions WHERE started_at > "
        f"DATEADD(day, -30, CAST('{settings.synth.end_date}' AS TIMESTAMP))") / n)
    # Affected subscriber shares, not event shares. Resolve subscription-only events via subscriptions.
    affected = {k: [] for k in ('nulls', 'duplicates', 'late_arriving')}
    for table, ts in EVENT_TIMES.items():
        source = f'{schema}.{table} t'
        customer = 't.customer_id'
        if table == 'subscription_events':
            source += f' JOIN {schema}.subscriptions s ON s.subscription_id=t.subscription_id'
            customer = 's.customer_id'
        optional = {'orders': 'blend_sku', 'subscription_events': 'event_type',
                    'email_events': 'campaign_id', 'support_tickets': 'sentiment_score',
                    'quiz_responses': 'answer_value'}[table]
        predicates = {
            'nulls': f't.{optional} IS NULL',
            'late_arriving': f't.loaded_at > DATEADD(day, 1, t.{ts})',
            'duplicates': f't.{KEYS[table]} IN (SELECT {KEYS[table]} FROM {schema}.{table} '
                          f'GROUP BY {KEYS[table]} HAVING COUNT(*) > 1)',
        }
        for category, predicate in predicates.items():
            affected[category].append(f'SELECT {customer} customer_id FROM {source} WHERE {predicate}')
    for category, queries in affected.items():
        add('edge_case', category, scalar('SELECT COUNT(*) FROM (' + ' UNION '.join(queries) + ')') / n)
    # All email timestamps are emitted in America/New_York (offset-bearing TIMESTAMP_TZ).
    add('edge_case', 'timezone_offsets', scalar(
        f'SELECT COUNT(DISTINCT customer_id) FROM {schema}.email_events') / n)
    timeline = wh.query(
        f"SELECT cancel_type, DATE_TRUNC('month', cancelled_at) month, COUNT(*) n "
        f'FROM {schema}.subscriptions WHERE cancelled_at IS NOT NULL GROUP BY 1,2 ORDER BY 2,1')
    for row in timeline.itertuples():
        add('churn_timeline', f'{row.month:%Y-%m}:{row.cancel_type}', row.n)
    result = pd.DataFrame(rows)
    result['profiled_at'] = datetime.now()
    result['scale'] = scale
    result['seed'] = settings.synth.seed
    return result


def write_profile(wh, settings, scale='tiny', path=None):
    frame = build_profile(wh, settings, scale)
    path = Path(path) if path is not None else settings.resolve('docs/data-profile.md')
    lines = ['# Synthetic data profile', '', f'Scale: {scale}; seed: {settings.synth.seed}.',
             'Measured from physical source tables; synthetic results are not real-world accuracy.', '',
             '| Section | Item | Value |', '|---|---|---:|']
    lines += [f'| {r.section} | {r.item} | {r.value:.6f} |' for r in frame.itertuples()]
    path.parent.mkdir(parents=True, exist_ok=True)
    with wh.transaction():
        wh.insert_frame(f'{settings.schemas.ops}.DATA_PROFILE', frame)
    path.write_text('\n'.join(lines) + '\n')
    return frame
