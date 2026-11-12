-- History reads filter by tenor and order by auction date. Idempotent; safe on existing volumes:
--   docker compose exec -T postgres psql -U zm_terminal -d zm_terminal < db/init/005_fixed_income_history_indexes.sql
CREATE INDEX IF NOT EXISTS treasury_bill_auctions_tenor_date_idx ON treasury_bill_auctions (tenor_days, auction_date);
CREATE INDEX IF NOT EXISTS government_bond_auctions_tenor_date_idx ON government_bond_auctions (tenor_years, auction_date);
