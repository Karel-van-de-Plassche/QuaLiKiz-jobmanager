"""
Copyright Dutch Institute for Fundamental Energy Research (2016)
Contributors: Karel van de Plassche (karelvandeplassche@gmail.com)
License: CeCILL v2.1
"""
# Suggestion: Run with python launch_run.py > launch_run.py.log 2>&1 &
import os
lockfile = 'launch_run.py.lock'
if os.path.exists(lockfile):
    exit('Lock file exists')
with open(lockfile, 'w') as lock:
    pass
import sqlite3
import warnings
from warnings import warn
import subprocess as sp
import tarfile
import shutil

from IPython import embed #pylint: disable=unused-import

from qualikiz_tools.qualikiz_io.qualikizrun import QuaLiKizBatch

def prepare_input(db, amount, mode='ordered', batchid=None):
    if mode == 'ordered':
        query = db.execute('''SELECT Id, Path FROM batch WHERE State='prepared'
                           LIMIT ?''', (str(amount),))
    elif mode == 'random':
        query = db.execute('''SELECT Id, Path FROM batch WHERE State='prepared'
                           ORDER BY RANDOM() LIMIT ?''', (str(amount),))
    elif mode == 'specific':
        query = db.execute('''SELECT Id, Path FROM batch WHERE State='prepared'
                           AND Id=? LIMIT ?''', (batchid, str(amount)))
    querylist = query.fetchall()
    for el in querylist:
        print('generating input for: ' + str(el))
        batchid = el[0]
        batchdir = el[1]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            batch = QuaLiKizBatch.from_dir(batchdir)
        batch.generate_input()
        for i, run in enumerate(batch.runlist):
            if run.inputbinaries_exist():
                db.execute('''UPDATE Job SET State='inputed'
                           WHERE Batch_id=? AND Job_id=?''',
                           (batchid, i))
                db.commit()
            else:
                raise Exception('Error: Generation of input binaries failed')
        db.execute('''UPDATE Batch SET State='inputed' WHERE Id=?''',
                   (batchid,))
        db.commit()


def queue(db, amount):
    query = db.execute('''SELECT Id, Path FROM batch WHERE State='inputed'
                       LIMIT ?''', (str(amount),))
    querylist = query.fetchall()
    for el in querylist:
        print('queueing: ' + str(el))
        batchid = el[0]
        batchdir = el[1]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            batch = QuaLiKizBatch.from_dir(batchdir)
        jobnumber = batch.queue_batch()
        if jobnumber:
            db.execute('''UPDATE Batch SET State='queued', Jobnumber=? WHERE Id=?''',
                       (jobnumber, batchid))
            db.execute('''UPDATE Job SET State='queued' WHERE Batch_Id=?''',
                       (batchid,))

            db.commit()

def waiting_jobs():
    output = sp.check_output(['sqs'])
    lines = output.splitlines()
    return len(lines) - 1


def finished_check(db):
    query = db.execute('''SELECT Id, Path, Jobnumber FROM batch WHERE State='queued' ''')
    querylist = query.fetchall()
    batch_notdone = 0
    for el in querylist:
        print('Checking ' + str(el))
        batchid = el[0]
        batchdir = el[1]
        jobnumber = el[2]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            batch = QuaLiKizBatch.from_dir(batchdir)
        output = sp.check_output(['sacct',
                                  '--brief', '--noheader', '--parsable2',
                                  '--job', str(jobnumber)])
        try:
            jobline = output.splitlines()[0]
        except IndexError:
            print('Something went wrong!')
            print(output)

        __, state, __ = jobline.split(b'|')
        print(state)
        if state == b'COMPLETED':
            batch_success = True
            for i, run in enumerate(batch.runlist):
                if run.is_done():
                    state = 'success'
                else:
                    state = 'failed'
                    batch_success = False
                db.execute('''UPDATE Job SET State=?, Note='Unknown' WHERE Batch_id=? AND Job_id=?''',
                           (state, batchid, i))
                db.commit()
            if batch_success:
                state = 'success'
            else:
                state = 'failed'
            db.execute('''UPDATE Batch SET State=?, Note='Unknown' WHERE Id=?''',
                       (state, batchid))
            db.commit()
        elif state.startswith(b'CANCELLED'):
            for i, run in enumerate(batch.runlist):
                if run.is_done():
                    db_state = 'success'
                else:
                    db_state = 'failed'
                db.execute('''UPDATE Job SET State=?, Note='CANCELLED'
                           WHERE Batch_id=? AND Job_id=?''',
                           (db_state, batchid, i))
                db.commit()
            db.execute('''UPDATE Batch SET State='failed', Note='CANCELLED'
                       WHERE Id=?''', (batchid,))
            db.commit()
        elif state == (b'TIMEOUT'):
            for i, run in enumerate(batch.runlist):
                if run.is_done():
                    db_state = 'success'
                else:
                    db_state = 'failed'
                db.execute('''UPDATE Job SET State=?, Note='TIMEOUT'
                           WHERE Batch_id=? AND Job_id=?''',
                           (db_state, batchid, i))
                db.commit()
            db.execute('''UPDATE Batch SET State='failed', Note='TIMEOUT'
                       WHERE Id=?''', (batchid,))
            db.commit()

        else:
            batch_notdone += 1
    print(str(batch_notdone) + ' not done')

def archive(db, limit):
    query = db.execute('''SELECT Id, Path, Jobnumber FROM batch
                       WHERE State='netcdfized' LIMIT ?''', (limit, ))
    querylist = query.fetchall()
    for el in querylist:
        print('Archiving: ' + str(el))
        batchid = el[0]
        batchdir = el[1]
        batchsdir, name = os.path.split(batchdir)
        netcdf_path = os.path.join(batchdir, name + '.nc')
        os.rename(netcdf_path, os.path.join(batchsdir, name + '.nc'))
        with tarfile.open(batchdir + '.tar', 'w') as tar:
            tar.add(batchdir, arcname=os.path.basename(batchdir))
        if os.path.isfile(batchdir + '.tar'):
            shutil.rmtree(batchdir)
            db.execute('''UPDATE Batch SET State='archived' WHERE Id=?''',
                       (batchid, ))
            db.commit()


def netcdfize(db, limit):
    query = db.execute('''SELECT Id, Path, Jobnumber FROM batch
                       WHERE State='success' LIMIT ?''', (limit,))
    querylist = query.fetchall()
    for el in querylist:
        print(el)
        batchid = el[0]
        batchdir = el[1]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            batch = QuaLiKizBatch.from_dir(batchdir)
        batch.to_netcdf()
        for i, run in enumerate(batch.runlist):
            print('Archiving ' + run.rundir)
            with tarfile.open(run.rundir + '.tar.gz', 'w:gz') as tar:
                tar.add(run.rundir, arcname=os.path.basename(run.rundir))
            if os.path.isfile(run.rundir + '.tar.gz'):
                shutil.rmtree(run.rundir)
                db.execute('''UPDATE Job SET State='archived'
                           WHERE Batch_id=? AND Job_id=?''',
                           (batchid, i))
                db.commit()
        db.execute('''UPDATE Batch SET State='netcdfized' WHERE Id=?''', (batchid, ))
        db.commit()

def cancel(db, criteria):
    query = db.execute('''SELECT Id, Path, Jobnumber FROM batch
                       WHERE State='queued' AND ''' + criteria) 
    querylist = query.fetchall()
    for el in querylist:
        print(el)
        batchid = el[0]
        batchdir = el[1]
        jobnumber = el[2]
        output = sp.check_output(['scancel', str(jobnumber)])
        state = 'cancelled'
        db.execute('''UPDATE Batch SET State=?
                   WHERE Id=?''',
                   (state, batchid))
        db.execute('''UPDATE Job SET State=?
                   WHERE Batch_id=?''',
                   (state, batchid ))
        db.commit()

def hold(db, criteria):
    query = db.execute('''SELECT Id, Path, Jobnumber FROM batch
                       WHERE State='inputed' OR State='prepared' AND ''' + criteria) 
    querylist = query.fetchall()
    for el in querylist:
        print(el)
        batchid = el[0]
        batchdir = el[1]
        jobnumber = el[2]
        state = 'hold'
        db.execute('''UPDATE Batch SET State=?
                   WHERE Id=?''',
                   (state, batchid))
        db.execute('''UPDATE Job SET State=?
                   WHERE Batch_id=?''',
                   (state, batchid ))
        db.commit()

def tar(db, criteria, limit):
    query = db.execute('''SELECT Id, Path, Jobnumber FROM batch
                       WHERE ''' + criteria + ''' LIMIT ?''', (limit, )) 
    querylist = query.fetchall()
    for el in querylist:
        print(el)
        batchid = el[0]
        batchdir = el[1]
        jobnumber = el[2]
        with tarfile.open(batchdir + '.tar.gz', 'w:gz') as tar:
            tar.add(batchdir, arcname=os.path.basename(batchdir))

def trash(db):
    resp = input('Warning: This operation is destructive! Are you sure? [Y/n]')
    if resp == '' or resp == 'Y' or resp == 'y':
        query = db.execute('''SELECT Id, Path from batch WHERE State='prepared' ''')
        querylist = query.fetchall()
        for el in querylist:
            print(el)
            batchid = el[0]
            batchdir = el[1]
            try:
                shutil.rmtree(batchdir)
            except FileNotFoundError:
                warn(dir + ' already gone')
            db.execute('''UPDATE Batch SET State='thrashed' WHERE Id=?''', (batchid, ))
            db.commit()




queuelimit = 100
jobdb = sqlite3.connect('jobdb.sqlite3')
in_queue = waiting_jobs()
numsubmit = max(0, queuelimit-in_queue)
print(str(in_queue) + ' jobs in queue. Submitting ' + str(numsubmit))
prepare_input(jobdb, numsubmit)
queue(jobdb, numsubmit)
#cancel(jobdb, 'Ti_Te_rel<=0.5')
#hold(jobdb, 'Ti_Te_rel<=0.5')
#tar(jobdb, 'Ti_Te_rel==0.5', 2)
finished_check(jobdb)
netcdfize(jobdb, 1)
finished_check(jobdb)
#archive(jobdb, 1)
#trash(jobdb)
jobdb.close()

print('Script done')
print(os.listdir())
os.remove(lockfile)
exit()
