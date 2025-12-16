'use client';
import { redirect } from 'next/navigation';
import React, { useState, useRef, useEffect } from 'react';


export default function Home() {
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [referenceTarget, setReferenceTarget] = useState('all');
  const [queryTarget, setQueryTarget] = useState('subset');
  const [query, setQuery] = useState('');
  const [fingerprintType, setFingerprintType] = useState('ecfp4');
  const [referenceCustomTargets, setReferenceCustomTargets] = useState('');
  const [querySubsetTargets, setQuerySubsetTargets] = useState('');
  const [queryCustom, setQueryCustom] = useState('');
  const advancedMenuRef = useRef(null);
  const [isLoading, setIsLoading] = useState(false);

  function toggleAdvancedMenu() {
    setShowAdvanced(!showAdvanced);
  }

  useEffect(() => {
    if (showAdvanced && advancedMenuRef.current) {
      advancedMenuRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
      advancedMenuRef.current.focus();
    }
  }, [showAdvanced]);

  function validateInputs(inputString) {
    // Basic validation: check if input is not empty and contains valid characters
    const isValid = inputString.trim().length > 0;
    return isValid;
  }

  async function handleSubmit() {
    setIsLoading(true);
    const formData = {
      query,
      fingerprint_type: fingerprintType,
      reference_target: referenceTarget,
      reference_custom_targets: referenceCustomTargets,
      query_target: queryTarget,
      query_subset_targets: querySubsetTargets,
      query_custom: queryCustom
    };
    
    console.log('Form submitted with data:', formData);
    // Here you can handle the form submission, e.g., send data to a server
    const resp = await fetch('/api/submit', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(formData),
    });
    const result = await resp.json();
    // setIsLoading(false);
    console.log('Server response:', result.task_id);
    if (result.task_id) {
      redirect(`/result/${result.task_id}`);
    } else {
      setIsLoading(false);
      alert('Failed to submit task. Please try again.');
    }

  } 

  const AdvancedMenu = () => {
    return (
      <div 
        ref={advancedMenuRef}
        tabIndex="-1"
        className="relative inline-block text-left w-full text-amber-950 px-10 py-6 mt-5 border border-gray-300 bg-teal-50 rounded-md shadow-sm focus:outline-none focus:ring-2" 
        id='advanced-menu'
      >
        <h3 className='text-2xl mb-4'>Advanced Options</h3>
        
        <div className="mt-4">
          <label className="block font-medium text-gray-700">
            1. Select Fingerprint Type:
          </label>
          <select 
            value={fingerprintType}
            onChange={(e) => setFingerprintType(e.target.value)}
            className="mt-2 w-full max-w-2xl px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-200 focus:border-blue-100"
          >
            <option value="ecfp4">RDKit_ECF4 : Extended Connectivity Fingerprint (Path-based, Bits, Hashed)</option>
          </select>
        </div>

        <div className="mt-6">
          <fieldset>
            <legend className="font-medium text-gray-700 mb-3">2. Select Reference Targets:</legend>
            
            <div className="space-y-4">
              <div>
                <label className="flex items-start">
                  <input 
                    type="radio" 
                    value="all"
                    checked={referenceTarget === 'all'}
                    onChange={(e) => setReferenceTarget(e.target.value)}
                    className="mt-1 w-4 h-4 border-gray-300 focus:ring-2 focus:ring-blue-300"
                  />
                  <span className="ml-2 text-sm font-medium text-gray-900">All targets (default)</span>
                </label>
              </div>

              <div>
                <label className="flex items-start">
                  <input 
                    type="radio" 
                    value="custom"
                    checked={referenceTarget === 'custom'}
                    onChange={(e) => setReferenceTarget(e.target.value)}
                    className="mt-1 w-4 h-4 border-gray-300 focus:ring-2 focus:ring-blue-300"
                  />
                  <span className="ml-2 text-sm font-medium text-gray-900">Custom target list</span>
                </label>
                <textarea 
                  value={referenceCustomTargets}
                  onChange={(e) => setReferenceCustomTargets(e.target.value)}
                  rows={4}
                  disabled={referenceTarget !== 'custom'}
                  placeholder="Enter ChEMBL target IDs separated by commas"
                  className="mt-2 ml-6 w-full max-w-xl p-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-200 focus:border-blue-100 disabled:bg-gray-100 disabled:cursor-not-allowed"
                />
              </div>
            </div>
          </fieldset>
        </div>

        <div className="mt-6">
          <fieldset>
            <legend className="font-medium text-gray-700 mb-3">3. Select Query:</legend>
            
            <div className="space-y-4">
              <div>
                <label className="flex items-start">
                  <input 
                    type="radio" 
                    value="subset"
                    checked={queryTarget === 'subset'}
                    onChange={(e) => setQueryTarget(e.target.value)}
                    className="mt-1 w-4 h-4 border-gray-300 focus:ring-2 focus:ring-blue-300"
                  />
                  <span className="ml-2 text-sm font-medium text-gray-900">Restrict to a subset of library targets (Optional)</span>
                </label>
                <textarea 
                  value={querySubsetTargets}
                  onChange={(e) => setQuerySubsetTargets(e.target.value)}
                  rows={4}
                  disabled={queryTarget !== 'subset'}
                  placeholder="Enter ChEMBL target IDs separated by commas"
                  className="mt-2 ml-6 w-full max-w-xl p-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-200 focus:border-blue-100 disabled:bg-gray-100 disabled:cursor-not-allowed"
                />
              </div>

              <div>
                <label className="flex items-start">
                  <input 
                    type="radio" 
                    value="custom"
                    checked={queryTarget === 'custom'}
                    onChange={(e) => setQueryTarget(e.target.value)}
                    className="mt-1 w-4 h-4 border-gray-300 focus:ring-2 focus:ring-blue-300"
                  />
                  <span className="ml-2 text-sm font-medium text-gray-900">Custom Query:</span>
                </label>
                <textarea 
                  value={queryCustom}
                  onChange={(e) => setQueryCustom(e.target.value)}
                  rows={4}
                  disabled={queryTarget !== 'custom'}
                  placeholder="Specify compounds or targets"
                  className="mt-2 ml-6 w-full max-w-xl p-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-200 focus:border-blue-100 disabled:bg-gray-100 disabled:cursor-not-allowed"
                />
              </div>
            </div>
          </fieldset>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-8">
          <div className="px-4 py-5 bg-white rounded-lg border border-gray-200">
            <p className="font-semibold mb-3">
              To screen custom compounds, list each compound separately with these columns:
              (please don&apos;t include in [compound_id] any commas, spaces or tabs)
            </p>
            
            <pre className="whitespace-pre font-mono text-sm bg-gray-100 p-4 rounded-lg overflow-x-auto mb-3">
              {'<smiles> <compound_id>'}
            </pre>
            
            <p className="mb-2">ZINC ID can also be used. For example:</p>
            <pre className="whitespace-pre font-mono text-sm bg-gray-100 p-4 rounded-lg overflow-x-auto">
{`ZINC000431131405
ZINC000276896078
NCCc1c[nH]c2ccc(O)cc12  serotonin
NCCc1ccc(O)c(O)c1 dopamine
Oc1ccc(cc1O)[C@@H](O)CNC
NC[C@H](O)c1ccc(O)c(O)c1`}
            </pre>
          </div>

          <div className="px-4 py-5 bg-white rounded-lg border border-gray-200">
            <p className="font-semibold mb-3">
              To screen custom compounds with targets, list each compound with these columns:
              (please don&apos;t include in [compound_id] any commas, spaces or tabs) which will group the compounds by the target id into sets.
            </p>
            
            <pre className="whitespace-pre font-mono text-sm bg-gray-100 p-4 rounded-lg overflow-x-auto mb-3">
              {'<target_id> <smiles> <compound_id>'}
            </pre>
            
            <p className="mb-2">For example:</p>
            <pre className="whitespace-pre font-mono text-sm bg-gray-100 p-4 rounded-lg overflow-x-auto">
{`HCA1 C[C@H](O)C(=O)O lactate
HCA1 O=C(O)CCCO GHB
HCA1 O=C(O)c1cccnc1 niacin
HDAC1 C[C@@H](O)CC(=O)O BHB
HDAC1 [O-]C(=O)CCC butyrate`}
            </pre>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="flex-col items-center justify-center min-h-screen py-2 bg-transparent backdrop-blur-md shadow-lg">
      <div className="mt-5 px-10 py-5">
        <h1 className="text-5xl font-semibold tracking-tight text-balance text-teal-100 sm:text-7xl">
          Similarity Ensemble Approach
        </h1>
        <p className="mt-8 text-3xl font-medium text-pretty text-green-100 sm:text-xl/8">
          The Similarity Ensemble Approach (SEA) relates proteins based on the set-wise chemical similarity among their ligands. 
          It can be used to rapidly search large compound databases and to build cross-target similarity maps.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-8 mt-5">
          <div className="text-md pt-2">
            <p className="text-md pt-2">
              SEA is provided by the <a href="https://bkslab.org" className="underline hover:text-blue-300">Shoichet Laboratory</a> in the Department of Pharmaceutical Chemistry at the University of California, San Francisco (UCSF).<br/>
              You can try SEA yourself via the online SEA search tool.
            </p>
            <h2 className="font-semibold mt-4 mb-2">If you use the result in SEA, please cite the following:</h2>
            <blockquote className="pl-4 py-2 px-2 border-l-4 border-gray-300">
              <p className="pl-4 pt-2">
                Keiser MJ, Roth BL, Armbruster BN, Ernsberger P, Irwin JJ, Shoichet BK. Relating protein pharmacology by ligand chemistry. <i>Nat Biotech</i> <b>25</b> (2), 197-206 (2007).<br/>
                <a className="px-2 italic hover:underline" href="http://www.ncbi.nlm.nih.gov/entrez/query.fcgi?db=pubmed&cmd=Retrieve&dopt=AbstractPlus&list_uids=17287757&query_hl=1&itool=pubmed_docsum">Pubmed</a>|
                <a className="px-2 italic hover:underline" href="https://doi.org/10.1038/nbt1284">DOI</a>|
                <a className="px-2 italic hover:underline" href="https://files.docking.org/bkslab/publications/keiser_2007.pdf">PDF</a>
              </p>
            </blockquote>
          </div>

          <div className="text-md pt-2">
            <h2 className="font-semibold mb-2">Caveat Emptor</h2>
            <p className="text-md">
              SEA is provided free-of-charge in the hope that it will be useful, but you must use it at your own risk. We make no guarantees about data confidentiality on this public service website.<br/>
              If you would like to use SEA securely, please contact support+sea*bkslab*org.<br/>
              To determine whether SEA&rsquo;s predictions for your compounds are already known, we recommend you visit the extensive K<sub>i</sub> Database at the NIMH Psychoactive Drug Screening Program.
            </p>
          </div>
        </div>

        <div className="mt-8">
          <div>
            <h2 className="text-3xl font-medium text-pretty text-green-100 sm:text-xl/8">Run SEA</h2>
            <button 
              type="button"
              onClick={toggleAdvancedMenu}
              className="text-md font-semibold text-white hover:underline mt-2"
            >
              Advanced Options <span aria-hidden="true">→</span>
            </button>

            <div>
              {showAdvanced ? (
                <AdvancedMenu />
              ) : (
                <div className="mt-2">
                  <textarea
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Paste SMILES or try example below"
                    className="mt-2 py-2 px-3 w-full border backdrop-blur-2xl border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-200 focus:border-blue-100"
                  />
                </div>
              )}
            </div>
          </div>

          {!showAdvanced && (
            <div className="mt-3">
              <p className="text-pretty mb-2">Examples:</p> 
              <p className="text-pretty">
                CNCCC(c1ccccc1)Oc2ccc(cc2)C(F)(F)F fluoxetine<br/>
                Cn1cnc2c1c(=O)n(c(=O)n2C)C caffeine
              </p> 
            </div>
          )}

          <div className="mt-10 flex items-center justify-center gap-x-6">
            <button
              onClick={handleSubmit}
              className="rounded-md bg-blue-400 px-3.5 py-2.5 text-md font-semibold text-white shadow-xs hover:bg-indigo-400 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500"
              disabled={isLoading}
            >
                  Submit
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}