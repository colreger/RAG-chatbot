-- Fictional local fixture. psql reads the generated reader secret without
-- writing it into source control or printing it as a query result.
\set ON_ERROR_STOP on
\set reader_password `cat /run/secrets/reader_password`
BEGIN;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE TABLE IF NOT EXISTS public.claims (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    status TEXT NOT NULL,
    deduction_reason TEXT NOT NULL
);
INSERT INTO public.claims VALUES
    ('RK-1042', 'gandalf', 'Teilweise erstattet', 'Minibar ist eine private Ausgabe.')
ON CONFLICT (id) DO NOTHING;
SELECT format('CREATE ROLE claims_reader LOGIN PASSWORD %L', :'reader_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'claims_reader')
\gexec
GRANT CONNECT ON DATABASE claims_demo TO claims_reader;
GRANT USAGE ON SCHEMA public TO claims_reader;
GRANT SELECT ON public.claims TO claims_reader;
COMMIT;
\unset reader_password
