# SEA Webserver 2025

This (preliminary) version of the SEA web server provides an implementation a stand-alone web application for running SEA searches and viewing results



How to set up SEA for development

1.  Set up Conda envrironment and run flask App

     <pre>conda env create -f environment.yml</pre>
     <pre>conda activate chemcommons</pre>
     <pre>pip install -r requirements.txt</pre>

     Create .env file and put these lines in making sure you replace the appropriate variables
     <pre>
        SEA_DATA_FOLDER="/nfs/home/khtang/tmp/sea225/v2/bioactive_prep"
        SEA_SCRIPT="/nfs/home/khtang/tmp/sea225/v2/run_sea_precalc.py"
        SEA_JOB_DIR="/nfs/home/khtang/tmp/sea225/v2/jobs"   
     </pre>
2.  Start REDIS server
     <pre>redis-server --port 6380 --daemonize yes</pre>
3.  Run these commands in separate terminal
    
     <pre>uvicorn backend.app.api:app --host 0.0.0.0 --port 5050 --reload<pre>
     <pre>celery -A backend.app.celery_worker worker --loglevel=info --concurrency=20  --max-tasks-per-child=30</pre>

4. Set up React App
     <pre>
        cd frontend
        npm install
        npm run dev
     </pre>

