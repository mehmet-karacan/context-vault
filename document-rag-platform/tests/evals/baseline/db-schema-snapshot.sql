--
-- PostgreSQL database dump
--

\restrict aks5hVmGnI9RLpSisGlqXfADR0oNLgbx5JBgAyuDkysRlSCQokNWJqK9WsvDSAv

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg12+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg12+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: chunks; Type: TABLE; Schema: public; Owner: raguser
--

CREATE TABLE public.chunks (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    embedding public.vector(1024)
);


ALTER TABLE public.chunks OWNER TO raguser;

--
-- Name: documents; Type: TABLE; Schema: public; Owner: raguser
--

CREATE TABLE public.documents (
    id uuid NOT NULL,
    project_id uuid NOT NULL,
    name character varying NOT NULL,
    size integer NOT NULL,
    status character varying NOT NULL,
    error_message character varying,
    uploaded_at timestamp without time zone NOT NULL
);


ALTER TABLE public.documents OWNER TO raguser;

--
-- Name: projects; Type: TABLE; Schema: public; Owner: raguser
--

CREATE TABLE public.projects (
    id uuid NOT NULL,
    name character varying NOT NULL,
    created_at timestamp without time zone NOT NULL
);


ALTER TABLE public.projects OWNER TO raguser;

--
-- Name: chunks chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: raguser
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: raguser
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: projects projects_name_key; Type: CONSTRAINT; Schema: public; Owner: raguser
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_name_key UNIQUE (name);


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: public; Owner: raguser
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);


--
-- Name: chunks_embedding_idx; Type: INDEX; Schema: public; Owner: raguser
--

CREATE INDEX chunks_embedding_idx ON public.chunks USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: chunks chunks_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: raguser
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: documents documents_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: raguser
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict aks5hVmGnI9RLpSisGlqXfADR0oNLgbx5JBgAyuDkysRlSCQokNWJqK9WsvDSAv

