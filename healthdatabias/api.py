from pydantic import ValidationError
from healthdatabias.database import OMOPCDMDatabase, BiasDatabase
from healthdatabias.cohort import CohortAction
from healthdatabias.config import load_config


class BIAS:
    _instance = None

    def __init__(self):
        self.config = {}
        self.bias_db = BiasDatabase()
        self.omop_cdm_db = None
        self.cohort_action = None

    def __new__(cls, config_file_path=None):
        if cls._instance is None:
            cls._instance = super(BIAS, cls).__new__(cls)
            cls._instance.set_config(config_file_path)
        return cls._instance

    def set_config(self, config_file_path: str):
        if config_file_path is None:
            print('no configuration file specified. '
                  'Call set_config(config_file_path) next to specify configurations')
        else:
            try:
                self.config = load_config(config_file_path)
                print(f'configuration specified in {config_file_path} loaded successfully')
            except FileNotFoundError:
                print('specified configuration file does not exist. '
                      'Call set_config(config_file_path) next to specify a valid '
                      'configuration file')
            except ValidationError as ex:
                print(f'configuration yaml file is not valid with validation error: {ex}')

    def set_root_omop(self):
        if not self.config:
            print('no valid configuration to set root OMOP CDM data. '
                  'Call set_config(config_file_path) to specify configurations first.')
        elif 'root_omop_cdm_database' in self.config:
            user = self.config['root_omop_cdm_database']['username']
            password = self.config['root_omop_cdm_database']['password']
            host = self.config['root_omop_cdm_database']['hostname']
            port = self.config['root_omop_cdm_database']['port']
            db = self.config['root_omop_cdm_database']['database']
            db_url = f"postgresql://{user}:{password}@{host}:{port}/{db}"
            self.omop_cdm_db = OMOPCDMDatabase(db_url)
            # load postgres extension in duckdb bias_db so that cohorts in duckdb can be joined
            # with OMOP CDM tables in omop_cdm_db
            self.bias_db.load_postgres_extension()
            self.bias_db.omop_cdm_db_url = db_url
        else:
            print('Configuration file must include configuration values for root_omop_cdm_database key.')

    def _set_cohort_action(self):
        if self.omop_cdm_db is None:
            print('A valid OMOP CDM must be set before creating a cohort. '
                  'Call set_root_omop first to set a valid root OMOP CDM')
            return None
        if self.cohort_action is None:
            self.cohort_action = CohortAction(self.omop_cdm_db, self.bias_db)
        return self.cohort_action

    def create_cohort(self, cohort_name, cohort_desc, query, created_by):
        c_action = self._set_cohort_action()
        if c_action:
            created_cohort = c_action.create_cohort(cohort_name, cohort_desc, query, created_by)
            print('cohort created successfully')
            return created_cohort
        else:
            print('failed to create a valid cohort action object')
            return None

    def compare_cohorts(self, cohort_id1, cohort_id2):
        c_action = self._set_cohort_action()
        if c_action:
            return c_action.compare_cohorts(cohort_id1, cohort_id2)
        else:
            print('failed to create a valid cohort action object')

    def cleanup(self):
        self.bias_db.close()
        self.omop_cdm_db.close()
        del self.cohort_action
