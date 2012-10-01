#!/bin/bash

#setup virtualenvironment 
export VIRTUALENVWRAPPER_PYTHON=/usr/bin/python2.7
export WORKON_HOME=/mnt/jenkins_backups/virtual_envs
if [ ! -d $WORKON_HOME ]; then
  mkdir $WORKON_HOME
fi
source /usr/local/bin/virtualenvwrapper.sh

mkvirtualenv zipline
workon zipline
pip install -r ./etc/requirements.txt
pip install -r ./etc/requirements_dev.txt

# Show what we have installed
pip freeze

#documentation output
paver apidocs html
pycco ./zipline/*.py -d ./docs/_build/html/pycco/
pycco ./zipline/finance/*.py -d ./docs/_build/html/pycco/finance
pycco ./zipline/test/*.py -d ./docs/_build/html/pycco/test
pycco ./zipline/transforms/*.py -d ./docs/_build/html/pycco/transforms
# use our custom css
cp ./etc/pycco.css ./docs/_build/html/pycco/
cp ./etc/pycco.css ./docs/_build/html/pycco/finance
cp ./etc/pycco.css ./docs/_build/html/pycco/test
cp ./etc/pycco.css ./docs/_build/html/pycco/transforms

#run all the tests in test. see setup.cfg for flags.
nosetests --config=jenkins_setup.cfg -I test_optimize

#run pylint checks
cp ./pylint.rcfile /mnt/jenkins/.pylintrc #default location for config file...
pylint -f parseable zipline > pylint.out

#run sloccount analysis
sloccount --wide --details ./zipline  > sloccount.sc

deactivate
