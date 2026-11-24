SELECT
    (SELECT count(*) FROM papers) AS papers,
    (SELECT count(*) FROM deposits) AS deposits,
    (SELECT count(*) FROM samples) AS samples,
    (SELECT count(*) FROM paper_files WHERE coalesce(pdf_path, '') <> '') AS pdfs;
