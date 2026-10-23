"""Independent source-quality perturbations, applied after clean emissions."""
import numpy as np
import pandas as pd

EDGE_CATEGORIES = ('no_quiz', 'no_email', 'no_tickets', 'single_order', 'new_subscriber',
                   'nulls', 'duplicates', 'late_arriving', 'timezone_offsets')
EVENT_TIMES = {'orders': 'ordered_at', 'subscription_events': 'event_ts',
               'email_events': 'event_ts', 'support_tickets': 'created_at',
               'quiz_responses': 'answered_at'}
KEYS = {'orders': 'order_id', 'subscription_events': 'event_id', 'email_events': 'event_id',
        'support_tickets': 'ticket_id', 'quiz_responses': 'answer_id'}


def apply(frames, seed):
    rng = np.random.default_rng([seed, 4245])
    customers = frames['subscriptions'].customer_id.to_numpy()
    for name, rate in [('quiz_responses', .15), ('email_events', .05), ('support_tickets', .65)]:
        missing = rng.choice(customers, max(1, int(len(customers) * rate)), replace=False)
        frames[name] = frames[name][~frames[name].customer_id.isin(missing)].reset_index(drop=True)
    single = rng.choice(customers, max(1, int(len(customers) * .02)), replace=False)
    orders = frames['orders']
    frames['orders'] = orders[~orders.customer_id.isin(single) | ~orders.customer_id.duplicated()].copy()
    for name in EVENT_TIMES:
        frame = frames[name].copy().reset_index(drop=True)
        if frame.empty:
            continue
        # Null only optional non-key fields, never dates or join keys.
        optional = {'orders': 'blend_sku', 'subscription_events': 'event_type',
                    'email_events': 'campaign_id', 'support_tickets': 'sentiment_score',
                    'quiz_responses': 'answer_value'}[name]
        null = rng.choice(len(frame), max(1, int(len(frame) * .01)), replace=False)
        frame.loc[null, optional] = None
        late = rng.choice(len(frame), max(1, int(len(frame) * .01)), replace=False)
        frame.loc[late, 'loaded_at'] += pd.Timedelta(days=7)
        duplicate = frame.iloc[rng.choice(len(frame), max(1, int(len(frame) * .005)), replace=False)].copy()
        duplicate['loaded_at'] += pd.Timedelta(hours=1)
        frames[name] = pd.concat([frame, duplicate], ignore_index=True)
    return frames
