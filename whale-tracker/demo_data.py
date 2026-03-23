"""
Mock data for --demo mode. Mirrors the shape of real Polymarket API responses.
"""

DEMO_MARKETS = [
    {
        "condition_id": "0xabc123def456aaa111222333444555666777888",
        "question": "Will Bitcoin be above $100,000 on March 31, 2026?",
        "slug": "will-btc-above-100k-march-2026",
        "yes_price": 0.62,
        "no_price": 0.38,
        "volume": 4_820_000,
        "volume_24h": 312_000,
        "liquidity": 980_000,
        "tokens": [
            {"token_id": "t_yes_1", "outcome": "YES", "price": 0.62},
            {"token_id": "t_no_1",  "outcome": "NO",  "price": 0.38},
        ],
        "active": True,
        "closed": False,
        "end_date": "2026-03-31T23:59:00Z",
    },
    {
        "condition_id": "0xdef789abc012bbb444555666777888999aaa111",
        "question": "Will Bitcoin be above $90,000 on April 1, 2026?",
        "slug": "will-btc-above-90k-april-2026",
        "yes_price": 0.78,
        "no_price": 0.22,
        "volume": 2_150_000,
        "volume_24h": 187_000,
        "liquidity": 540_000,
        "tokens": [
            {"token_id": "t_yes_2", "outcome": "YES", "price": 0.78},
            {"token_id": "t_no_2",  "outcome": "NO",  "price": 0.22},
        ],
        "active": True,
        "closed": False,
        "end_date": "2026-04-01T23:59:00Z",
    },
    {
        "condition_id": "0x111222333444555abc789def012ccc555666777",
        "question": "Will Bitcoin be higher than $95,000 on April 7, 2026?",
        "slug": "will-btc-higher-95k-april-7-2026",
        "yes_price": 0.71,
        "no_price": 0.29,
        "volume": 1_380_000,
        "volume_24h": 95_000,
        "liquidity": 310_000,
        "tokens": [
            {"token_id": "t_yes_3", "outcome": "YES", "price": 0.71},
            {"token_id": "t_no_3",  "outcome": "NO",  "price": 0.29},
        ],
        "active": True,
        "closed": False,
        "end_date": "2026-04-07T23:59:00Z",
    },
    {
        "condition_id": "0x222333444555666ddd890efg123hij456klm789",
        "question": "Will BTC close above $85,000 on March 28, 2026?",
        "slug": "will-btc-close-above-85k-march-28",
        "yes_price": 0.88,
        "no_price": 0.12,
        "volume": 890_000,
        "volume_24h": 43_000,
        "liquidity": 220_000,
        "tokens": [
            {"token_id": "t_yes_4", "outcome": "YES", "price": 0.88},
            {"token_id": "t_no_4",  "outcome": "NO",  "price": 0.12},
        ],
        "active": True,
        "closed": False,
        "end_date": "2026-03-28T23:59:00Z",
    },
    {
        "condition_id": "0x333444555666777eee901fgh234ijk567lmn890",
        "question": "Will Bitcoin drop below $70,000 before April 30, 2026?",
        "slug": "will-btc-drop-below-70k-april-2026",
        "yes_price": 0.09,
        "no_price": 0.91,
        "volume": 650_000,
        "volume_24h": 28_000,
        "liquidity": 170_000,
        "tokens": [
            {"token_id": "t_yes_5", "outcome": "YES", "price": 0.09},
            {"token_id": "t_no_5",  "outcome": "NO",  "price": 0.91},
        ],
        "active": True,
        "closed": False,
        "end_date": "2026-04-30T23:59:00Z",
    },
]

import whale_tracker as wt

DEMO_WHALES = [
    wt.Whale(
        address="0xA1B2C3D4E5F6789012345678901234567890ABCD",
        positions=[
            wt.WhalePosition(
                market_question="Will Bitcoin be above $100,000 on March 31, 2026?",
                condition_id="0xabc123def456aaa111222333444555666777888",
                outcome="YES",
                size=280_000,
                avg_price=0.55,
                current_price=0.62,
                usd_value=173_600,
                unrealized_pnl=19_600,
            ),
            wt.WhalePosition(
                market_question="Will Bitcoin be above $90,000 on April 1, 2026?",
                condition_id="0xdef789abc012bbb444555666777888999aaa111",
                outcome="YES",
                size=95_000,
                avg_price=0.70,
                current_price=0.78,
                usd_value=74_100,
                unrealized_pnl=7_600,
            ),
        ],
        total_volume=520_000,
    ),
    wt.Whale(
        address="0xDEADBEEF0123456789ABCDEF0123456789ABCDEF",
        positions=[
            wt.WhalePosition(
                market_question="Will Bitcoin drop below $70,000 before April 30, 2026?",
                condition_id="0x333444555666777eee901fgh234ijk567lmn890",
                outcome="NO",
                size=190_000,
                avg_price=0.88,
                current_price=0.91,
                usd_value=172_900,
                unrealized_pnl=5_700,
            ),
            wt.WhalePosition(
                market_question="Will Bitcoin be above $100,000 on March 31, 2026?",
                condition_id="0xabc123def456aaa111222333444555666777888",
                outcome="NO",
                size=200_000,
                avg_price=0.42,
                current_price=0.38,
                usd_value=76_000,
                unrealized_pnl=-8_000,
            ),
        ],
        total_volume=410_000,
    ),
    wt.Whale(
        address="0xFEEDFACE9876543210FEDCBA9876543210FEDCBA",
        positions=[
            wt.WhalePosition(
                market_question="Will Bitcoin be higher than $95,000 on April 7, 2026?",
                condition_id="0x111222333444555abc789def012ccc555666777",
                outcome="YES",
                size=250_000,
                avg_price=0.65,
                current_price=0.71,
                usd_value=177_500,
                unrealized_pnl=15_000,
            ),
        ],
        total_volume=280_000,
    ),
    wt.Whale(
        address="0xCAFEBABE1111222233334444555566667777CAFE",
        positions=[
            wt.WhalePosition(
                market_question="Will BTC close above $85,000 on March 28, 2026?",
                condition_id="0x222333444555666ddd890efg123hij456klm789",
                outcome="YES",
                size=110_000,
                avg_price=0.82,
                current_price=0.88,
                usd_value=96_800,
                unrealized_pnl=6_600,
            ),
            wt.WhalePosition(
                market_question="Will Bitcoin be above $100,000 on March 31, 2026?",
                condition_id="0xabc123def456aaa111222333444555666777888",
                outcome="NO",
                size=80_000,
                avg_price=0.45,
                current_price=0.38,
                usd_value=30_400,
                unrealized_pnl=-5_600,
            ),
        ],
        total_volume=195_000,
    ),
    wt.Whale(
        address="0x1234567890ABCDEF1234567890ABCDEF12345678",
        positions=[
            wt.WhalePosition(
                market_question="Will Bitcoin be above $90,000 on April 1, 2026?",
                condition_id="0xdef789abc012bbb444555666777888999aaa111",
                outcome="NO",
                size=220_000,
                avg_price=0.26,
                current_price=0.22,
                usd_value=48_400,
                unrealized_pnl=-8_800,
            ),
        ],
        total_volume=130_000,
    ),
]

# Recompute totals for demo whales
for w in DEMO_WHALES:
    w.recompute_totals()
