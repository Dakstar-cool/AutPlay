package app.autplay.data.desktop.account

internal object AccountCatalogSchema {
    const val COMPONENT:String="catalog-projections-v1"
    private fun table(name:String,keys:String,columns:String,body:String):AccountTableShape=AccountTableShape(name,keys,columns,2,"CREATE TABLE $name ($body)")
    val tables:List<AccountTableShape> = listOf(
        table("j_catalog_artist","scope_id,artist_id","scope_id,artist_id,version,name,sort_name,artist_type,disambiguation,country_code,identity_status,deleted_at,snapshot_id,source_sequence","""
            scope_id TEXT NOT NULL,artist_id TEXT NOT NULL,version INTEGER NOT NULL,name TEXT NOT NULL,sort_name TEXT,artist_type TEXT,
            disambiguation TEXT,country_code TEXT,identity_status TEXT,deleted_at TEXT,snapshot_id TEXT,source_sequence INTEGER,
            PRIMARY KEY(scope_id,artist_id),FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT"""),
        table("j_catalog_credit","scope_id,credit_id","scope_id,credit_id,version,display_name,member_count,deleted_at,snapshot_id,source_sequence","""
            scope_id TEXT NOT NULL,credit_id TEXT NOT NULL,version INTEGER NOT NULL,display_name TEXT NOT NULL,member_count INTEGER NOT NULL,
            deleted_at TEXT,snapshot_id TEXT,source_sequence INTEGER,PRIMARY KEY(scope_id,credit_id),FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT"""),
        table("j_catalog_credit_member","scope_id,credit_id,version,position","scope_id,credit_id,version,position,artist_id,credited_name,join_phrase,role","""
            scope_id TEXT NOT NULL,credit_id TEXT NOT NULL,version INTEGER NOT NULL,position INTEGER NOT NULL,artist_id TEXT NOT NULL,
            credited_name TEXT NOT NULL,join_phrase TEXT NOT NULL,role TEXT NOT NULL,PRIMARY KEY(scope_id,credit_id,version,position),
            FOREIGN KEY(scope_id,credit_id) REFERENCES j_catalog_credit(scope_id,credit_id) ON DELETE RESTRICT"""),
        table("j_catalog_subject_credit","scope_id,subject_type,subject_id","scope_id,subject_type,subject_id,credit_id,version,owner_scope_id,page_count,deleted_at,snapshot_id,source_sequence","""
            scope_id TEXT NOT NULL,subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,credit_id TEXT NOT NULL,version INTEGER NOT NULL,
            owner_scope_id TEXT NOT NULL CHECK(length(owner_scope_id)=64 AND owner_scope_id NOT GLOB '*[^0-9a-f]*'),page_count INTEGER NOT NULL,
            deleted_at TEXT,snapshot_id TEXT,source_sequence INTEGER,PRIMARY KEY(scope_id,subject_type,subject_id),
            FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT"""),
        table("j_catalog_owner_page","scope_id,subject_type,subject_id,credit_id,version,owner_scope_id,page","scope_id,subject_type,subject_id,credit_id,version,owner_scope_id,page,page_count,record_count,exact_payload","""
            scope_id TEXT NOT NULL,subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,credit_id TEXT NOT NULL,version INTEGER NOT NULL,
            owner_scope_id TEXT NOT NULL,page INTEGER NOT NULL,page_count INTEGER NOT NULL,record_count INTEGER NOT NULL,
            exact_payload BLOB NOT NULL CHECK(length(exact_payload)<=262144),PRIMARY KEY(scope_id,subject_type,subject_id,credit_id,version,owner_scope_id,page),
            FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT"""),
        table("j_catalog_owner_recording","scope_id,subject_type,subject_id,credit_id,version,owner_scope_id,recording_id","scope_id,subject_type,subject_id,credit_id,version,owner_scope_id,recording_id,page","""
            scope_id TEXT NOT NULL,subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,credit_id TEXT NOT NULL,version INTEGER NOT NULL,
            owner_scope_id TEXT NOT NULL,recording_id TEXT NOT NULL,page INTEGER NOT NULL,
            PRIMARY KEY(scope_id,subject_type,subject_id,credit_id,version,owner_scope_id,recording_id),
            FOREIGN KEY(scope_id,subject_type,subject_id,credit_id,version,owner_scope_id,page)
                REFERENCES j_catalog_owner_page(scope_id,subject_type,subject_id,credit_id,version,owner_scope_id,page) ON DELETE RESTRICT"""),
        table("j_track_metadata","scope_id,track_id","scope_id,track_id,revision,state,exact_document","""
            scope_id TEXT NOT NULL,track_id TEXT NOT NULL,revision INTEGER NOT NULL,state TEXT NOT NULL,
            exact_document BLOB NOT NULL CHECK(length(exact_document)<=262144),PRIMARY KEY(scope_id,track_id),
            FOREIGN KEY(scope_id,track_id) REFERENCES j_track_ref(scope_id,track_ref_id) ON DELETE RESTRICT"""),
        table("j_metadata_album","scope_id,track_id","scope_id,track_id,metadata_revision,evidence_key,provider,release_id,title,album_artist,release_date,disc_number,track_number,state","""
            scope_id TEXT NOT NULL,track_id TEXT NOT NULL,metadata_revision INTEGER NOT NULL,evidence_key TEXT,provider TEXT,release_id TEXT,
            title TEXT,album_artist TEXT,release_date TEXT,disc_number INTEGER,track_number INTEGER,state TEXT NOT NULL,
            PRIMARY KEY(scope_id,track_id),FOREIGN KEY(scope_id,track_id) REFERENCES j_track_metadata(scope_id,track_id) ON DELETE RESTRICT"""),
        table("j_attribution","scope_id,source_id,aggregate_type,aggregate_id,observation_kind","scope_id,source_id,aggregate_type,aggregate_id,exact_document,observation_kind","""
            scope_id TEXT NOT NULL,source_id TEXT NOT NULL,aggregate_type TEXT NOT NULL,aggregate_id TEXT NOT NULL,
            exact_document BLOB NOT NULL CHECK(length(exact_document)<=262144),observation_kind TEXT NOT NULL,PRIMARY KEY(scope_id,source_id,aggregate_type,aggregate_id,observation_kind),
            FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT"""),
    )
    val create:List<String> = tables.map{it.ddl}+tables.map{
        "CREATE TRIGGER ${it.name}_retain BEFORE DELETE ON ${it.name} BEGIN SELECT RAISE(ABORT,'ACCOUNT_RETAINED'); END"
    }+listOf("j_catalog_credit_member","j_catalog_owner_page","j_catalog_owner_recording","j_attribution").map{
        "CREATE TRIGGER ${it}_immutable BEFORE UPDATE ON $it BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END"
    }
}
