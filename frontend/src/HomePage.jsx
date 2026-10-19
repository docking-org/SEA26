import { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import 'altcha';

import Layout from "./components/Layout"

function HomePage() {
  const navigate = useNavigate();
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [referenceTarget, setReferenceTarget] = useState('all');
  const [queryTarget, setQueryTarget] = useState('subset');
  const [query, setQuery] = useState('');
  const [fingerprintType, setFingerprintType] = useState('ecfp4');
  const [referenceCustomTargets, setReferenceCustomTargets] = useState('');
  const [querySubsetTargets, setQuerySubsetTargets] = useState('');
  const [queryCustom, setQueryCustom] = useState('');
  const advancedMenuRef = useRef(null);
  const altchaRef = useRef(null);
  const [altchaPayload, setAltchaPayload] = useState(null);
  const [altchaVerified, setAltchaVerified] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isValid, setIsValid] = useState(true);
  const [hideAdvanceMenu, sethideAdvanceMenu] = useState(true);
  const [errorMessage, setErrorMessage] = useState(''); // NEW: error message state
  const [bannerVisible, setBannerVisible] = useState(true);

  const [altchaDisabled, setAltchaDisabled] = useState(null);

  useEffect(() => {
    fetch('/api/captcha/challenge')
      .then(r => r.json())
      .then(data => {
        if (data.disabled) {
          setAltchaDisabled(true);
          setAltchaVerified(true);
        } else {
          setAltchaDisabled(false);
        }
      })
      .catch(() => { setAltchaDisabled(false); });
  }, []);

  useEffect(() => {
    const widget = altchaRef.current;
    if (!widget) return;
    const handleStateChange = (ev) => {
      if (ev.detail.state === 'verified' && ev.detail.payload) {
        setAltchaPayload(ev.detail.payload);
        setAltchaVerified(true);
      } else {
        setAltchaPayload(null);
        setAltchaVerified(false);
      }
    };
    widget.addEventListener('statechange', handleStateChange);
    return () => widget.removeEventListener('statechange', handleStateChange);
  }, []);

  function toggleAdvancedMenu() {
    setShowAdvanced(!showAdvanced);
  }

  // Process input to add compound IDs if missing
  function processInput(inputString) {
    if (!inputString || inputString.trim().length === 0) {
      return inputString;
    }

    const lines = inputString.trim().split('\n');
    const processedLines = lines.map((line, index) => {
      const trimmedLine = line.trim();
      if (!trimmedLine) return trimmedLine; // Skip empty lines
      
      // Split by whitespace (spaces or tabs)
      const columns = trimmedLine.split(/\s+/);
      
      // If only 1 column (just SMILES or ZINC ID), add a unique compound ID
      if (columns.length === 1) {
        const compoundId = `compound_${index + 1}`;
        return `${trimmedLine} ${compoundId}`;
      }
      
      // Otherwise, keep the original line
      return trimmedLine;
    });

    return processedLines.join('\n');
  }

  // NEW: improved validation function
  function validateInputs(inputString) {
    if (!inputString || inputString.trim().length === 0) {
      setIsValid(false);
      setErrorMessage('Please enter at least one SMILES or compound.');
      return false;
    }
    const lineCount = inputString.trim().split('\n').filter(l => l.trim()).length;
    if (lineCount > 10) {
      setIsValid(false);
      setErrorMessage(`Query is limited to 10 compounds. You entered ${lineCount}.`);
      return false;
    }
    setIsValid(true);
    setErrorMessage('');
    return true;
  }

  // NEW: clear error when user starts typing
  function handleQueryChange(e) {
    setQuery(e.target.value);
    if (errorMessage) {
      setErrorMessage('');
      setIsValid(true);
    }
  }

  async function handleSubmit() {
    // Validate before submitting
    if (!validateInputs(query)) {
      return;
    }

    if (!altchaDisabled && !altchaPayload) {
      setErrorMessage('Please complete the CAPTCHA verification first.');
      return;
    }

    setIsLoading(true);

    // Process input to add compound IDs if needed
    const processedQuery = processInput(query);

    const formData = {
      query: processedQuery,
      fingerprint_type: fingerprintType,
      reference_target: referenceTarget,
      reference_custom_targets: referenceCustomTargets,
      query_target: queryTarget,
      query_subset_targets: querySubsetTargets,
      query_custom: queryCustom,
      altcha: altchaPayload,
    };
    console.log('Form submitted with data:', formData['query']);

    try {
      const resp = await fetch('/api/submit', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(formData),
      });

      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({}));
        setIsLoading(false);
        setErrorMessage(errorData.detail || 'Submission failed. Please try again.');
        if (altchaRef.current) altchaRef.current.reset();
        setAltchaPayload(null);
        setAltchaVerified(false);
        return;
      }

      const result = await resp.json();
      console.log('Server response:', result.task_id);
      if (result.task_id) {
        navigate(`/result?taskId=${result.task_id}`);
      } else {
        setIsLoading(false);
        setErrorMessage(resp.detail || 'Unexpected response from server. Please try again.');
        setErrorMessage('Failed to submit task. Please try again.');
      }
    } catch (error) {
      setIsLoading(false);
      setErrorMessage('Network error. Please try again.');
      if (altchaRef.current) altchaRef.current.reset();
      setAltchaPayload(null);
      setAltchaVerified(false);
    }
  } 
  

  const AdvancedMenu = () => {
    return (
      <div 
        ref={advancedMenuRef}
        // tabIndex="-1"
        className="relative inline-block text-left w-full text-amber-950 px-4 py-4 sm:px-10 sm:py-6 mt-5 border border-gray-300 bg-teal-50 rounded-md shadow-sm focus:outline-none focus:ring-2"
        id='advanced-menu'
      >
        <h3 className='text-xl sm:text-2xl mb-4'>Advanced Options</h3>
        
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
            <pre className="whitespace-pre font-mono text-sm text-teal-950 bg-gray-100 p-4 rounded-lg overflow-x-auto">
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
    <Layout>
        {bannerVisible && (
          <div className="bg-teal-400 backdrop-blur-sm text-teal-950 px-4 py-3 text-center text-lg relative rounded-t-lg -mx-6 -mt-6 mb-6 border-b border-white/20">
            <span>SEA has been updated to ChEMBL {import.meta.env.VITE_CHEMBL_VERSION} for improved prediction accuracy. The previous version remains available <a target="_blank" href="https://sea16.docking.org" className="underline hover:text-teal-200">here</a>.</span>
            <button
              onClick={() => setBannerVisible(false)}
              className="absolute right-4 top-1/2 -translate-y-1/2 text-white/80 hover:text-white text-lg leading-none cursor-pointer"
              aria-label="Dismiss banner"
            >
              &times;
            </button>
          </div>
        )}
        {/* Your main application components go here */}
        <div className="text-right text-xs text-teal-300/70">ChEMBL library version: {import.meta.env.VITE_CHEMBL_VERSION}</div>
        <div className="flex-col items-center justify-center min-h-screen py-2">
        <div className="mt-5 px-4 py-4 sm:px-10 sm:py-5">
          <h1 className="text-3xl font-semibold tracking-tight text-balance text-teal-100 sm:text-5xl lg:text-7xl">
            Similarity Ensemble Approach
          </h1>
          <p className="mt-6 text-base font-medium text-pretty text-green-100 sm:text-lg md:text-xl/8">
            The Similarity Ensemble Approach (SEA) relates proteins based on the set-wise chemical similarity among their ligands. 
            It can be used to rapidly search large compound databases and to build cross-target similarity maps.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-8 mt-5">
            <div className="text-sm sm:text-md pt-2">
              <p className="text-sm sm:text-md pt-2">
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

            <div className="text-sm sm:text-md pt-2">
              <h2 className="font-semibold mb-2">Caveat Emptor</h2>
              <p className="text-sm sm:text-md">
                SEA is provided free-of-charge in the hope that it will be useful, but you must use it at your own risk. We make no guarantees about data confidentiality on this public service website.<br/>
                If you would like to use SEA securely, please contact support+sea*bkslab*org.<br/>
                To determine whether SEA&rsquo;s predictions for your compounds are already known, we recommend you visit the extensive K<sub>i</sub> Database at the NIMH Psychoactive Drug Screening Program.
              </p>
            </div>
          </div>

          <div className="mt-8">
            <div>
              <h2 className="text-xl font-medium text-pretty text-green-100 sm:text-xl/8">Run SEA</h2>
              <div>
              {hideAdvanceMenu ? (
                <p></p>

              ) : (
                
                <button 
                  type="button"
                  onClick={toggleAdvancedMenu}
                  className="text-md font-semibold text-white hover:underline mt-2"
                >
                  Advanced Options <span aria-hidden="true">→</span>
              </button>
              )}
              </div>
            
              <div>
                {showAdvanced ? (
                  <AdvancedMenu />
                ) : (
                  <div className="mt-2">
                    <textarea
                      value={query}
                      onChange={handleQueryChange}
                      placeholder="Paste SMILES or try example below"
                      className={`mt-2 py-2 px-3 w-full border backdrop-blur-2xl rounded-md text-white focus:outline-none focus:ring-2 focus:ring-blue-200 focus:border-blue-100 ${
                        !isValid ? 'border-red-500 bg-red-50' : 'border-gray-300'
                      }`}
                    />
                    {/* Error message display */}
                    {errorMessage && (
                      <p className="mt-2 text-red-400 text-sm font-medium bg-lime-100 border-rounded-md p-2">
                        ⚠️ {errorMessage}
                      </p>
                    )}
                  </div>
                )}
              </div>
            </div>

            {!showAdvanced && (
              <div className="mt-3">
                <p className="text-teal-950 mb-2">Examples:</p>
                <p className="text-pretty text-sm sm:text-base text-teal-950 break-all sm:break-norma bg-white/50 p-3 rounded-md">
                  <pre>
                  COc1ccc2[nH]cc(c2c1)C(CC)CN<br/>
                  Cc1ccc2c3c1C(CN3CCNC2)CC<br/>
                  CCOc1cc(c(cc1I)OC)CC(C)N<br/>
                  </pre>
                  
                </p>
                <button
                  type="button"
                  onClick={() => {
                    setQuery("COc1ccc2[nH]cc(c2c1)C(CC)CN\nCc1ccc2c3c1C(CN3CCNC2)CC\nCCOc1cc(c(cc1I)OC)CC(C)N");
                    setErrorMessage('');
                    setIsValid(true);
                  }}
                  className="mt-2 rounded-md bg-teal-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-teal-500"
                >
                  Load Example
                </button>
              </div>
            )}

            {/* ALTCHA Captcha Widget */}
            {altchaDisabled === false && (
              <div className="mt-6 flex justify-center">
                <altcha-widget
                  ref={altchaRef}
                  challengeurl="/api/captcha/challenge"
                ></altcha-widget>
              </div>
            )}

            <div className="mt-10 flex items-center justify-center gap-x-6">
              {isLoading ? (
                <div className="flex items-center gap-2">
                  <button className="rounded-md bg-indigo-500 px-3.5 py-2.5 text-md font-semibold text-white shadow-xs focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500 flex items-center gap-2" disabled>
                    <div className="w-6 h-6 border-4 border-white text-teal-50 border-t-transparent rounded-full animate-spin"></div>
                    <span className="text-teal-50 font-semibold">Submitting...</span>
                  </button>
                </div>
              ) : (
                <button
                  onClick={handleSubmit}
                  disabled={isLoading || query.trim().length === 0 || (!altchaDisabled && !altchaVerified)}
                  className={`rounded-md px-3.5 py-2.5 text-md font-semibold text-white shadow-xs focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500 ${
                    query.trim().length === 0 || (!altchaDisabled && !altchaVerified)
                      ? 'bg-gray-400 cursor-not-allowed'
                      : 'bg-blue-400 hover:bg-indigo-400'
                  }`}
                >
                  Submit
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </Layout>
  )
}
export default HomePage
