import duckdb
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import create_engine
from healthdatabias.models import Cohort, CohortDefinition
from healthdatabias.sql import *

class BiasDatabase:
    distribution_queries = {
        "age": AGE_DISTRIBUTION_QUERY,
        "gender": GENDER_DISTRIBUTION_QUERY,
    }
    stats_queries = {
        "age": AGE_STATS_QUERY,
        "gender": GENDER_STATS_QUERY,
        "race": RACE_STATS_QUERY,
        "ethnicity": ETHNICITY_STATS_QUERY
    }
    _instance = None  # indicating a singleton with only one instance of the class ever created
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(BiasDatabase, cls).__new__(cls, *args, **kwargs)
            cls._instance._initialize()  # Initialize only once
        return cls._instance

    def _initialize(self):
        # by default, duckdb uses in memory database
        self.conn = duckdb.connect(':memory:')
        self.omop_cdm_db_url = None
        self._create_cohort_definition_table()
        self._create_cohort_table()

    def _create_cohort_definition_table(self):
        self.conn.execute('CREATE SEQUENCE id_sequence START 1')
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS cohort_definition (
                      id INTEGER DEFAULT nextval('id_sequence'), 
                      name VARCHAR NOT NULL, 
                      description VARCHAR, 
                      created_date DATE, 
                      creation_info VARCHAR, 
                      created_by VARCHAR,
                      PRIMARY KEY (id)
                      )
                ''')
        print("Cohort Definition table created.")

    def _create_cohort_table(self):
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS cohort (
                subject_id BIGINT,
                cohort_definition_id INTEGER,
                cohort_start_date DATE,
                cohort_end_date DATE,
                PRIMARY KEY (cohort_definition_id, subject_id),
                FOREIGN KEY (cohort_definition_id) REFERENCES cohort_definition(id)
            )
        ''')
        print("Cohort table created.")

    def load_postgres_extension(self):
        self.conn.execute("INSTALL postgres_scanner;")
        self.conn.execute("LOAD postgres_scanner;")

    def create_cohort_definition(self, cohort_definition: CohortDefinition):
        self.conn.execute('''
            INSERT INTO cohort_definition (name, description, created_date, creation_info, created_by)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            cohort_definition.name,
            cohort_definition.description,
            cohort_definition.created_date or datetime.now(),
            cohort_definition.creation_info,
            cohort_definition.created_by
        ))
        print("Cohort definition inserted successfully.")
        self.conn.execute("SELECT id from cohort_definition ORDER BY id DESC LIMIT 1")
        created_cohort_id = self.conn.fetchone()[0]
        return created_cohort_id

    # Method to insert cohort data
    def create_cohort(self, cohort: Cohort):
        self.conn.execute('''
            INSERT INTO cohort (subject_id, cohort_definition_id, cohort_start_date, cohort_end_date)
            VALUES (?, ?, ?, ?)
        ''', (
            cohort.subject_id,
            cohort.cohort_definition_id,
            cohort.cohort_start_date,
            cohort.cohort_end_date
        ))

    def get_cohort_definition(self, cohort_definition_id):
        results = self.conn.execute(f'''
        SELECT id, name, description, created_date, creation_info, created_by FROM cohort_definition 
        WHERE id = {cohort_definition_id} 
        ''')
        headers = [desc[0] for desc in results.description]
        row = results.fetchall()
        if len(row) == 0:
            return {}
        else:
            return dict(zip(headers, row[0]))

    def get_cohort(self, cohort_definition_id):
        results = self.conn.execute(f'''
        SELECT subject_id, cohort_definition_id, cohort_start_date, cohort_end_date FROM cohort 
        WHERE cohort_definition_id = {cohort_definition_id}
        ''')
        headers = [desc[0] for desc in results.description]
        rows = results.fetchall()
        return [dict(zip(headers, row)) for row in rows]

    def _create_person_table(self):
        if self.omop_cdm_db_url is not None:
            # need to create person table from OMOP CDM postgreSQL database
            self.conn.execute(f"""
                CREATE TABLE IF NOT EXISTS person AS 
                SELECT * from postgres_scan('{self.omop_cdm_db_url}', 'public', 'person')
            """)
            return True # success
        else:
            return False # failure

    def _execute_query(self, query_str):
        results = self.conn.execute(query_str)
        headers = [desc[0] for desc in results.description]
        rows = results.fetchall()
        if len(rows) == 0:
            return []
        else:
            return [dict(zip(headers, row)) for row in rows]

    def get_cohort_basic_stats(self, cohort_definition_id: int, variable=''):
        """
        Get aggregation statistics for a cohort from the cohort table.
        :param cohort_definition_id: cohort definition id representing the cohort
        :param variable: optional with an empty string as default. If empty, basic stats of
        the cohort are returned; If set to a specific variable such as age, gender, race,
        the stats of the specified variable in the cohort are returned
        :return: cohort stats corresponding to the specified variable
        """
        try:
            if variable:
                if self._create_person_table():
                    query_str = self.__class__.stats_queries.get(variable)
                    if query_str is None:
                        raise ValueError(f"Statistics for variable '{variable}' is not available. "
                                         f"Valid variables are {self.__class__.stats_queries.keys()}")
                    stats_query = query_str.format(cohort_definition_id)
                else:
                    print(f"Cannot connect to the OMOP database to query person table")
                    return None
            else:
                # Query the cohort data to get basic statistics
                stats_query = f'''
                    WITH cohort_Duration AS (
                        SELECT
                            subject_id,
                            cohort_start_date,
                            cohort_end_date,
                            cohort_end_date - cohort_start_date AS duration_days
                        FROM
                            cohort
                        WHERE cohort_definition_id = {cohort_definition_id}    
                    )
                    SELECT
                        COUNT(*) AS total_count,
                        MIN(cohort_start_date) AS earliest_start_date,
                        MAX(cohort_start_date) AS latest_start_date,
                        MIN(cohort_end_date) AS earliest_end_date,
                        MAX(cohort_end_date) AS latest_end_date,
                        MIN(duration_days) AS min_duration_days,
                        MAX(duration_days) AS max_duration_days,
                        ROUND(AVG(duration_days), 2) AS avg_duration_days,
                        CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_days) AS INT) AS median_duration,
                        ROUND(STDDEV(duration_days), 2) AS stddev_duration
                    FROM cohort_Duration                    
                '''
            return self._execute_query(stats_query)

        except Exception as e:
            print(f"Error computing cohort basic statistics: {e}")
            return None

    @property
    def cohort_distribution_variables(self):
        return self.__class__.distribution_queries.keys()

    def get_cohort_distributions(self, cohort_definition_id: int, variable: str):
        """
        Get age distribution statistics for a cohort from the cohort table.
        """
        try:
            if self._create_person_table():
                query_str = self.__class__.distribution_queries.get(variable)
                if query_str is None:
                    raise ValueError(f"Distribution for variable '{variable}' is not available. "
                                     f"Valid variables are {self.__class__.distribution_queries.keys()}")
                query = query_str.format(cohort_definition_id)
                return self._execute_query(query)
            else:
                print(f"Cannot connect to the OMOP database to query person table")
                return None
        except Exception as e:
            print(f"Error computing cohort {variable} distributions: {e}")
            return None

    def close(self):
        self.conn.close()
        BiasDatabase._instance = None
        print("Connection to BiasDatabase closed.")


class OMOPCDMDatabase:
    _instance = None  # indicating a singleton with only one instance of the class ever created
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(OMOPCDMDatabase, cls).__new__(cls)
            cls._instance._initialize(*args, **kwargs)  # Initialize only once
        return cls._instance

    def _initialize(self, db_url):
        try:
            self.engine = create_engine(
                db_url,
                echo=False,
                connect_args={'options': '-c default_transaction_read_only=on'}  # Enforce read-only transactions
            )
            self.Session = sessionmaker(bind=self.engine)
            print("Connected to the OMOP CDM database (read-only).")
        except SQLAlchemyError as e:
            print(f"Failed to connect to the database: {e}")

    def get_session(self):
        # Provide a new session for read-only queries
        return self.Session()

    def close(self):
        # Dispose of the connection (if needed)
        self.engine.dispose()
        OMOPCDMDatabase._instance = None
        print("Connection to the OMOP CDM database closed.")
