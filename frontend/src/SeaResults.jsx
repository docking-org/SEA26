import React, { useState, useEffect, useMemo } from 'react';
import { useParams, useSearchParams, Link } from 'react-router-dom';
import Layout from './components/Layout';


export default function Result() {
    const [searchParams] = useSearchParams();
    const taskId = searchParams.get('taskId') || '';
    const targetChemblId = searchParams.get('targetChemblId') || '';
    const [targetName, setTargetName] = useState('');
    const [Uniprot_ID, setUniprot_ID] = useState('');
    const [Uniprot_Accession, setUniprot_Accession] = useState('');
    const [percent1Done, setPercent1Done] = useState(0);
    const [result, setResult] = useState([]);
    const [queryCompounds, setQueryCompounds] = useState([]);
    const [inputCompounds, setInputCompounds] = useState([]);
    const [filteredData, setFilteredData] = useState([]);
    const [heatmapData, setHeatmapData] = useState(null);
    const [seaData, setSeaData] = useState(null);
    const [hoveredCell, setHoveredCell] = useState(null);
    const [sortBy, setSortBy] = useState('score');
    const [sortDirection, setSortDirection] = useState('desc');
    const [resultSortBy, setResultSortBy] = useState('p_value');
    const [resultSortDir, setResultSortDir] = useState('asc');
    const [activeButton, setActiveButton] = useState(false);
    const [taskStatus, setTaskStatus] = useState(null);
    const [errorMessage, setErrorMessage] = useState(null);
    const [boltzStatus, setBoltzStatus] = useState(undefined); // undefined = never fetched; null = fetched, no boltz jobs
    const [boltzJobs, setBoltzJobs] = useState({}); // job_key -> { chembl_id, ligand, status }

    // RDkit images
    const [rdkitReady, setRdkitReady] = useState(false);
    const [moleculeImages, setMoleculeImages] = useState({});

  
    function generateMoleculeSVG(smiles, id, width = 200, height = 200) {
        try {
            if (!window.RDKit || !rdkitReady) {
                console.log('RDKit not ready yet');
                return null;
            }

            const mol = window.RDKit.get_mol(smiles);
            if (!mol || !mol.is_valid()) {
                console.error('Invalid molecule:', smiles);
                return null;
            }

            const svg = mol.get_svg_with_highlights(JSON.stringify({
                width,
                height,
                backgroundColour: [0, 0, 0, 0]
            }));
            mol.delete();
            
            setMoleculeImages(prev => ({
                ...prev,
                [id]: svg
            }));
            
            return svg;
        } catch (error) {
            console.error('Error generating SVG for', smiles, error);
            return null;
        }
    }
    useEffect(() => {
        if (heatmapData && rdkitReady && window.RDKit) {
            heatmapData.forEach((item) => {
                if (!moleculeImages[item.Known_Hit_ID]) {
                    generateMoleculeSVG(item.Known_Hit_SMILES, item.Known_Hit_ID, 300, 300);
                }
            });
        }
    }, [heatmapData, rdkitReady]);

    useEffect(() => {
        if (inputCompounds.length > 0 && rdkitReady && window.RDKit) {
            inputCompounds.forEach((entry) => {
                const idx = entry.indexOf(' ');
                const smiles = idx >= 0 ? entry.slice(0, idx) : entry;
                const compoundId = idx >= 0 ? entry.slice(idx + 1) : entry;
                if (!moleculeImages[compoundId]) {
                    generateMoleculeSVG(smiles, compoundId, 200, 200);
                }
            });
        }
    }, [inputCompounds, rdkitReady]);

    async function checkTaskStatus(){
        try {
            const response = await fetch(`/api/result?taskId=${taskId}`);
            const data = await response.json();

            setTaskStatus(data.status);
            setBoltzStatus(data.boltz_status ?? null); // null means fetched but no boltz jobs
            if (data.boltz_status) {
                fetch(`/api/boltz_job_status?task_id=${taskId}`)
                    .then(r => r.json())
                    .then(jobs => setBoltzJobs(jobs || {}))
                    .catch(() => {});
            }
            if (data.status === 'EXPIRED') {
                setErrorMessage(data.error || 'Job results are no longer available.');
                return;
            }
            if (data.status === 'SUCCESS'){
                setPercent1Done(100);
                setResult(data.result);
                if (data.input) setInputCompounds(data.input);
                // Cache result in sessionStorage for quick access on revisit
                try {
                    sessionStorage.setItem(`sea_result_${taskId}`, JSON.stringify(data.result));
                    if (data.input) sessionStorage.setItem(`sea_input_${taskId}`, JSON.stringify(data.input));
                } catch (e) {
                    console.warn('Failed to cache result in sessionStorage:', e);
                }
                console.log('Task completed:', data);
            }
            else if (data.status === 'ERROR') {
                setErrorMessage(data.error || 'Task failed.');
                console.log('Task failed:', data);
            }
            else if (data.status === 'RUNNING' || data.status === 'PENDING') {
                setPercent1Done(data.progress ?? 0);
                // Show results as soon as sea_result.tsv is written (Phase 1 post-processing done)
                if (data.result && data.result.length > 0) {
                    setResult(data.result);
                    if (data.input) setInputCompounds(data.input);
                }
                console.log('Task in progress:', data);
            } else {
                console.log('Task status:', data);
            }

        } catch (error) {
            console.error('Error checking task status:', error);
        }
    }

    async function fetchTargetHeatMap(targetChemblId, taskId) {
        try {
            const response = await fetch(`/api/best_similarity?taskId=${taskId}&target_chembl_id=${targetChemblId}`);
            const data = await response.json();
            console.log('Target heat map data:', data);
            if (data.target_name) setTargetName(data.target_name);
            setSeaData(data.best_similarity);
            setHeatmapData(data.best_similarity);
            setQueryCompounds(data.query_compounds);
        } catch (error) {
            console.error('Error fetching target heat map data:', error);
        }
    }

    function handleHeatMapDisplay(compoundId) {
        console.log('handleHeatMapDisplay called with compoundId:', compoundId);
        if (!compoundId) {
            console.log('Resetting heatmap data to all compounds');
            setHeatmapData(seaData);
            return;
        }
        setHeatmapData(seaData); // Reset to all data first
        if (compoundId)
            console.log('seaData:', seaData);
            console.log('Filtering heatmap data for compound:', compoundId);
            setHeatmapData(seaData.filter(item => item.Name === compoundId));
            console.log('Filtered heatmap data:', heatmapData);
            setActiveButton(true);
        }




    const SORTABLE_RESULT_COLS = new Set(['MaxTc', 'Z_score', 'p_value']);

    function handleResultSort(column) {
        if (resultSortBy === column) {
            setResultSortDir(prev => prev === 'asc' ? 'desc' : 'asc');
        } else {
            setResultSortBy(column);
            setResultSortDir('desc');
        }
    }

    const sortedResult = useMemo(() => {
        if (!result || result.length === 0) return result;
        return [...result].sort((a, b) => {
            const av = parseFloat(a[resultSortBy]);
            const bv = parseFloat(b[resultSortBy]);
            return resultSortDir === 'desc' ? bv - av : av - bv;
        });
    }, [result, resultSortBy, resultSortDir]);

    function handleSort(column) {
        if (sortBy === column) {
            setSortDirection(prev => prev === 'asc' ? 'desc' : 'asc');
        } else {
            setSortBy(column);
            setSortDirection(column === 'score' ? 'desc' : 'asc');
        }
    }

    // Memoized sorted data
    const sortedData = useMemo(() => {
        if (!heatmapData|| heatmapData.length === 0) return [];
        const sorted = [...heatmapData];
        if (sortBy === 'score') {
            sorted.sort((a, b) => sortDirection === 'desc'
                ? b.Tanimoto_Score - a.Tanimoto_Score
                : a.Tanimoto_Score - b.Tanimoto_Score);
        } else {
            sorted.sort((a, b) => sortDirection === 'asc'
                ? a.Known_Hit_ID.localeCompare(b.Known_Hit_ID)
                : b.Known_Hit_ID.localeCompare(a.Known_Hit_ID));
        }
        return sorted;
    }, [heatmapData, sortBy, sortDirection]);

    // Memoized statistics
    const stats = useMemo(() => {
        if (!heatmapData || heatmapData.length === 0) return null;
        const scores = heatmapData.map(d => d.Tanimoto_Score);
        return {
            min: Math.min(...scores),
            max: Math.max(...scores),
            avg: scores.reduce((a, b) => a + b, 0) / scores.length
        };
    }, [heatmapData]);

    const getColor = (score) => {
        if (score >= 0.9) return 'rgb(0, 100, 0)';
        if (score >= 0.7) return 'rgb(34, 139, 34)';
        if (score >= 0.5) return 'rgb(50, 205, 50)';
        if (score >= 0.3) return 'rgb(255, 215, 0)';
        if (score >= 0.2) return 'rgb(255, 165, 0)';
        return 'rgb(255, 69, 0)';
    };

    const getTextColor = (score) => {
        return score >= 0.5 ? 'white' : '#1f2937';
    };
    // On mount: load cached result immediately, then fetch fresh data to refresh cache
    useEffect(() => {
        try {
            const cached = sessionStorage.getItem(`sea_result_${taskId}`);
            if (cached) {
                setResult(JSON.parse(cached));
                setTaskStatus('SUCCESS');
                setPercent1Done(100);
            }
            const cachedInput = sessionStorage.getItem(`sea_input_${taskId}`);
            if (cachedInput) setInputCompounds(JSON.parse(cachedInput));
        } catch (e) {
            console.warn('Failed to load cached result:', e);
        }
    }, [taskId]);

    // Stable key derived from boltzStatus values — prevents the effect from re-firing
    // on every poll when the server returns a new object with unchanged values.
    const boltzStatusKey = boltzStatus === undefined ? 'undefined'
        : boltzStatus === null ? 'null'
        : `${boltzStatus.running}|${boltzStatus.queued}|${boltzStatus.done}|${boltzStatus.failed}`;

    // Poll task status; fast initially (500ms) then slow down to 3s once running
    useEffect(() => {
        if (taskStatus === 'EXPIRED') return;
        // undefined = never fetched → keep polling; null = no boltz jobs → settled
        const boltzSettled = boltzStatus !== undefined && (!boltzStatus || (boltzStatus.running === 0 && boltzStatus.queued === 0));
        if ((taskStatus === 'SUCCESS' || taskStatus === 'ERROR') && boltzSettled) return;
        checkTaskStatus();
        const isEarlyPhase = taskStatus === null || taskStatus === 'PENDING';
        const interval = setInterval(checkTaskStatus, isEarlyPhase ? 500 : 3000);
        return () => clearInterval(interval);
    }, [taskId, taskStatus, boltzStatusKey]);

    // Load RDKit.js
    useEffect(() => {
    const loadRDKit = async () => {
        try {
            if (!window.RDKit) {
                await new Promise((resolve, reject) => {
                    const script = document.createElement('script');
                    script.src = 'https://unpkg.com/@rdkit/rdkit/dist/RDKit_minimal.js';
                    script.onload = resolve;
                    script.onerror = reject;
                    document.head.appendChild(script);
                });
                
                window.initRDKitModule().then((RDKit) => {
                    window.RDKit = RDKit;
                    setRdkitReady(true);
                    console.log('RDKit.js loaded successfully');
                });
            } else {
                setRdkitReady(true);
            }
        } catch (error) {
            console.error('Error loading RDKit.js:', error);
        }
    };
    
        loadRDKit();
    }, []);

    // Fetch heatmap data when targetChemblId changes
    useEffect(() => {
        if (targetChemblId) {
            fetchTargetHeatMap(targetChemblId, taskId);
            // Look up target info from cached sea_result
            try {
                const cached = sessionStorage.getItem(`sea_result_${taskId}`);
                if (cached) {
                    const parsed = JSON.parse(cached);
                    const match = parsed.find(r => r.Target_Chembl_ID === targetChemblId);
                    if (match) {
                        setTargetName(match.Target_Name || '');
                        setUniprot_ID(match.Uniprot_ID || '');
                        setUniprot_Accession(match.Uniprot_Accession || '');
                    }
                }
            } catch (e) {
                console.warn('Failed to look up target name from cache:', e);
            }
        }
    }, [targetChemblId, taskId]);

    // Show heatmap view if targetChemblId is present
    if (targetChemblId) {
        if (!heatmapData || heatmapData.length === 0) {
            return (
                <div className="flex items-center justify-center min-h-screen">
                    <div className="p-8 bg-gray-50 rounded-lg">
                        <p className="text-gray-600 text-center">Loading similarity data...</p>
                    </div>
                </div>
            );
        }
        if (!rdkitReady) {
            return (
                <div className="flex items-center justify-center min-h-screen">
                    <div className="p-8 bg-gray-50 rounded-lg">
                        <p className="text-gray-600 text-center">Loading molecule svg...</p>
                    </div>
                </div>
            );
        }
        
        // Render heatmap view
        // Display all data or data for selected query compound
        return (
            <Layout>
            {/* <div className="min-h-screen bg-gray-100 p-4 sm:p-8 rounded-lg"> */}
            
                <div className="max-w-7xl mx-auto">
                    <Link 
                        to={`/result?taskId=${taskId}`}
                        className=" hover:underline mb-4 inline-block"
                    >
                        ← Back to result
                    </Link>
                    

                    <div className="bg-white rounded-lg shadow-lg p-4 sm:p-6 mt-4">
                     
                        <div className="flex flex-col md:flex-row md:justify-between md:items-start mb-4 gap-4">
                            <div>
                                <h2 className="text-xl sm:text-2xl font-bold text-gray-800">Tanimoto Similarity Results</h2>
                                <div className="flex flex-col md:flex-row md:items-center mb-6">
                                    <p className="text-md text-gray-600 mr-4">Query Compound:</p>
                                    <select
                                        className="px-3 py-4 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium"
                                        onChange={(e) => {
                                        const selectedCompound = e.target.value;
                                        if (selectedCompound === "all") {
                                            handleHeatMapDisplay(null);
                                        } else {
                                            handleHeatMapDisplay(selectedCompound);
                                        }
                                        }}
                                    >
                                        <option value="all">All query compounds </option>
                                        {queryCompounds.map((compound) => (
                                        <option key={compound} value={compound}>
                                            {compound}
                                        </option>
                                        ))}
                                    </select>
                                </div>
                            </div>
                            <div className=" rounded-lg p-4 text-xl text-gray-700 space-y-1 =">
                                <p><span className="font-semibold">Target ChEMBL ID:</span> {targetChemblId}</p>
                                <p><span className="font-semibold">Target Name:</span> {targetName}</p>
                                <p><span className="font-semibold">Uniprot ID:</span> {Uniprot_ID}</p>
                                <p><span className="font-semibold">Uniprot Accession:</span> {Uniprot_Accession}</p>
                            </div>
                        </div>
                        <div className="mt-6 p-4 bg-gray-50 rounded">
                            <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
                                <div>
                                    <h3 className="text-sm font-semibold text-gray-700 mb-2">Similarity Scale</h3>
                                    <div className="flex items-center gap-2">
                                        <span className="text-xs text-gray-600">Low</span>
                                        <div className="flex">
                                            {[0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0].map((val) => (
                                                <div
                                                    key={val}
                                                    className="w-8 h-6 sm:w-12 sm:h-8 border border-gray-300 flex items-center justify-center text-[10px] sm:text-xs font-semibold"
                                                    style={{ 
                                                        backgroundColor: getColor(val),
                                                        color: getTextColor(val)
                                                    }}
                                                    title={val.toFixed(1)}
                                                >
                                                    {val.toFixed(1)}
                                                </div>
                                            ))}
                                        </div>
                                        <span className="text-xs text-gray-600">High</span>
                                    </div>
                                </div>
                                {stats && (
                                    <div className="text-sm text-gray-600 space-y-1">
                                        <div><strong>Total Compounds:</strong> {heatmapData.length}</div>
                                        <div><strong>Score Range:</strong> {stats.min.toFixed(4)} - {stats.max.toFixed(4)}</div>
                                        <div><strong>Average Score:</strong> {stats.avg.toFixed(4)}</div>
                                    </div>
                                )}
                            </div>
                        </div>
                        
                        <div className="overflow-x-auto">
                            <table className="w-full border-collapse">
                                <thead>
                                    <tr>
                                        <th
                                            className="p-3 text-left text-sm font-semibold text-gray-700 bg-gray-100 sticky left-0 z-10 cursor-pointer select-none hover:bg-gray-200 transition-colors"
                                            onClick={() => handleSort('id')}
                                        >
                                            Known Hit ID <span style={{ opacity: sortBy === 'id' ? 1 : 0.3 }}>{sortBy === 'id' ? (sortDirection === 'asc' ? '▲' : '▼') : '▲'}</span>
                                        </th>
                                        <th
                                            className="p-3 text-center text-sm font-semibold text-gray-700 bg-gray-100 cursor-pointer select-none hover:bg-gray-200 transition-colors"
                                            onClick={() => handleSort('score')}
                                        >
                                            Tanimoto Score <span style={{ opacity: sortBy === 'score' ? 1 : 0.3 }}>{sortBy === 'score' ? (sortDirection === 'asc' ? '▲' : '▼') : '▼'}</span>
                                        </th>
                                        <th className="p-3 text-left text-sm font-semibold text-gray-700 bg-gray-100">
                                            SMILES
                                        </th>
                                        <th className="p-3 text-center text-sm font-semibold text-gray-700 bg-gray-100">
                                            Image
                                        </th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {sortedData.map((item, index) => (
                                        <tr 
                                            key={index}
                                            className="border-b hover:bg-gray-50 transition-colors"
                                        >
                                            <td className="p-3 text-md font-medium text-gray-900 sticky left-0 bg-white">
                                                {item.Known_Hit_ID}
                                            </td>
                                            <td 
                                                className="relative p-0"
                                                onMouseEnter={() => setHoveredCell(index)}
                                                onMouseLeave={() => setHoveredCell(null)}
                                            >
                                                <div
                                                    className="h-16 flex items-center justify-center text-md font-bold cursor-pointer transition-transform hover:scale-105"
                                                    style={{
                                                        backgroundColor: getColor(item.Tanimoto_Score),
                                                        color: getTextColor(item.Tanimoto_Score)
                                                    }}
                                                >
                                                    {item.Tanimoto_Score.toFixed(4)}
                                                </div>
                                                {hoveredCell === index && (
                                                    <div className="absolute z-20 bg-gray-800 text-white text-xs rounded px-3 py-2 top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 shadow-lg min-w-max pointer-events-none">
                                                        <div><strong>Compound:</strong> {item.Name}</div>
                                                        <div><strong>ID:</strong> {item.Known_Hit_ID}</div>
                                                        <div><strong>Score:</strong> {item.Tanimoto_Score}</div>
                                                    </div>
                                                )}
                                            </td>
                                            <td className="p-3 text-md text-gray-600 font-mono max-w-[150px] break-all" title={item.Known_Hit_SMILES}>
                                                {item.Known_Hit_SMILES}
                                            </td>
                                            <td className="p-3 text-center">
                                                {moleculeImages[item.Known_Hit_ID] ? (
                                                    <div
                                                        className="w-36 h-36 sm:w-48 sm:h-48 md:w-64 md:h-64 mx-auto"
                                                        dangerouslySetInnerHTML={{ __html: moleculeImages[item.Known_Hit_ID] }}
                                                    />
                                                ) : (
                                                    <p className="text-sm text-gray-500">Loading...</p>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                
                        <div className="mt-6 p-4 bg-gray-50 rounded">
                            <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
                                <div>
                                    <h3 className="text-sm font-semibold text-gray-700 mb-2">Similarity Scale</h3>
                                    <div className="flex items-center gap-2">
                                        <span className="text-xs text-gray-600">Low</span>
                                        <div className="flex">
                                            {[0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0].map((val) => (
                                                <div
                                                    key={val}
                                                    className="w-8 h-6 sm:w-12 sm:h-8 border border-gray-300 flex items-center justify-center text-[10px] sm:text-xs font-semibold"
                                                    style={{ 
                                                        backgroundColor: getColor(val),
                                                        color: getTextColor(val)
                                                    }}
                                                    title={val.toFixed(1)}
                                                >
                                                    {val.toFixed(1)}
                                                </div>
                                            ))}
                                        </div>
                                        <span className="text-xs text-gray-600">High</span>
                                    </div>
                                </div>
                                {stats && (
                                    <div className="text-sm text-gray-600 space-y-1">
                                        <div><strong>Total Compounds:</strong> {heatmapData.length}</div>
                                        <div><strong>Score Range:</strong> {stats.min.toFixed(4)} - {stats.max.toFixed(4)}</div>
                                        <div><strong>Average Score:</strong> {stats.avg.toFixed(4)}</div>
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            {/* </div> */}
            </Layout>
        );
    }

    // Main results page
    return (
        <Layout>
        <div className="flex-col items-center justify-center py-2 text-gray-700 ">
            <div className="px-4 py-4 sm:px-10 sm:py-5 bg-gray-50 rounded-lg" >
                <h1 className="text-2xl  font-bold mb-4">Result Page</h1>
                <p className="mb-2">Your task ID is: {taskId}</p>

                {taskStatus === 'EXPIRED' && (
                    <div className="p-5 bg-amber-50 border border-amber-200 rounded-lg text-amber-900">
                        <p className="font-semibold text-base mb-1">Results No Longer Available</p>
                        <p className="text-sm">Job results are automatically removed after 96 hours to free up storage. Please resubmit your query to generate new results.</p>
                    </div>
                )}

                {/* Progress Bar — hidden once expired */}
                {taskStatus !== 'EXPIRED' && taskStatus !== 'SUCCESS' && (
                    <p className="text-xs text-gray-500 bg-gray-100 rounded-md px-3 py-2 mb-3">
                        Identical queries are cached to increase efficiency and reduce redundant computation.
                    </p>
                )}
                {taskStatus !== 'EXPIRED' && <div className="mb-6">
                    <div className="flex justify-between items-center mb-1">
                        <span className="text-sm font-medium text-gray-700">Progress</span>
                        <span className="text-sm font-medium text-gray-700">{percent1Done}%</span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-4 overflow-hidden shadow-inner">
                        <div
                            className="h-full rounded-full transition-all duration-500 ease-out flex items-center justify-end pr-2"
                            style={{
                                width: `${percent1Done}%`,
                                backgroundColor: taskStatus === 'SUCCESS' ? '#10b981' : taskStatus === 'ERROR' ? '#ef4444' : '#3b82f6'
                            }}
                        >
                            {percent1Done > 10 && (
                                <span className="text-xs font-bold text-white">
                                    {taskStatus === 'SUCCESS' ? '✓' : ''}
                                </span>
                            )}
                        </div>
                    </div>
                    <p className="text-xs text-gray-500 mt-1">
                        {taskStatus === 'ERROR'
                            ? 'Task failed.'
                            : percent1Done === 100
                                ? 'Complete'
                                : percent1Done >= 98
                                    ? 'Post-processing output...'
                                    : 'Scoring targets...'}
                    </p>
                </div>}
                {taskStatus === 'ERROR' && (
                    <div className="p-4 bg-red-100 border-l-4 border-red-500 text-red-700 mb-4">
                        <p className="font-medium">Task Failed</p>
                        <p className="text-sm">{errorMessage}</p>
                    </div>
                )}
                {taskStatus === 'SUCCESS' && result.length === 0 && (
                    <div className="p-4 bg-yellow-100 border-l-4 border-yellow-500 text-yellow-700">
                        No results found for this task.
                    </div>
                )}
           
                {result.length > 0 && (
                    <div>
                        <div className="flex flex-col sm:flex-row gap-2 sm:gap-4 my-4">
                        <a
                            href={`/api/download?taskId=${taskId}`}
                            className="inline-block px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 font-bold no-underline text-sm sm:text-base text-center"
                            download
                        >
                            Download Results (ZIP)
                        </a>
                        <a
                            href={`/compoundVStarget?taskId=${taskId}`}
                            className="inline-block px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 font-bold no-underline text-sm sm:text-base text-center"
                        >
                            Compound VS Targets
                        </a>
                        </div>
                        {inputCompounds.length > 0 && (
                            <div className="mt-6">
                                <h2 className="mb-2 text-xl font-semibold">Input Compounds:</h2>
                                <div className="overflow-x-auto rounded-lg shadow mb-4">
                                    <table className="min-w-full divide-y divide-gray-200 bg-white">
                                        <thead className="bg-green-100">
                                            <tr>
                                                <th className="px-4 py-2 text-left text-xs font-bold text-green-700 uppercase tracking-wider border-b">Compound ID</th>
                                                <th className="px-4 py-2 text-left text-xs font-bold text-green-700 uppercase tracking-wider border-b">SMILES</th>
                                                <th className="px-4 py-2 text-center text-xs font-bold text-green-700 uppercase tracking-wider border-b">Structure</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {inputCompounds.map((entry, i) => {
                                                const idx = entry.indexOf(' ');
                                                const smiles = idx >= 0 ? entry.slice(0, idx) : entry;
                                                const compoundId = idx >= 0 ? entry.slice(idx + 1) : entry;
                                                return (
                                                    <tr key={i} className={i % 2 === 0 ? 'bg-green-50' : 'bg-white'}>
                                                        <td className="px-4 py-2 text-xs text-gray-700 border-b font-medium">{compoundId}</td>
                                                        <td className="px-4 py-2 text-xs text-gray-600 border-b font-mono break-all">{smiles}</td>
                                                        <td className="px-4 py-2 text-center border-b">
                                                            {moleculeImages[compoundId] ? (
                                                                <div
                                                                    className="w-32 h-32 mx-auto overflow-hidden"
                                                                    style={{ lineHeight: 0 }}
                                                                    dangerouslySetInnerHTML={{ __html: moleculeImages[compoundId].replace(/<svg /, '<svg style="width:100%;height:100%;" ') }}
                                                                />
                                                            ) : (
                                                                <span className="text-xs text-gray-400">Loading...</span>
                                                            )}
                                                        </td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        )}
                        {inputCompounds.length === 1 ? (
                            <p className="text-xs text-gray-500 bg-gray-100 rounded-md px-3 py-2 mt-4">
                                Boltz2 structure predictions are automatically queued for the top 5 novel targets.
                            </p>
                        ) : inputCompounds.length > 1 ? (
                            <p className="text-xs text-gray-500 bg-gray-100 rounded-md px-3 py-2 mt-4">
                                Boltz2 auto-predictions are only available for single-molecule submissions.
                            </p>
                        ) : null}
                        <div className="mt-4 mb-2 flex items-center gap-3 flex-wrap">
                            <h2 className="text-xl font-semibold">Results:</h2>
                            {boltzStatus && (() => {
                                const isActive = boltzStatus.running > 0 || boltzStatus.queued > 0;
                                const allDone = !isActive && boltzStatus.done > 0;
                                const allFailed = !isActive && boltzStatus.done === 0 && boltzStatus.failed > 0;
                                const parts = [];
                                if (boltzStatus.running > 0) parts.push({ label: `${boltzStatus.running} running`, color: 'text-blue-600' });
                                if (boltzStatus.queued  > 0) parts.push({ label: `${boltzStatus.queued} queued`,  color: 'text-gray-500' });
                                if (boltzStatus.done    > 0) parts.push({ label: `${boltzStatus.done} done`,     color: 'text-green-600' });
                                if (boltzStatus.failed  > 0) parts.push({ label: `${boltzStatus.failed} failed`, color: 'text-red-500' });
                                return (
                                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
                                        isActive  ? 'bg-blue-100 text-blue-700' :
                                        allDone   ? 'bg-green-100 text-green-700' :
                                        allFailed ? 'bg-red-100 text-red-700' :
                                                    'bg-gray-100 text-gray-600'
                                    }`}>
                                        {isActive && (
                                            <svg className="animate-spin h-3 w-3 shrink-0" viewBox="0 0 24 24" fill="none">
                                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
                                            </svg>
                                        )}
                                        {!isActive && allDone && <span>✓</span>}
                                        {!isActive && allFailed && <span>✗</span>}
                                        <span>Boltz-2{isActive ? ':' : allDone ? ' complete' : allFailed ? ' failed' : ''}</span>
                                        {parts.map((p, i) => (
                                            <span key={i} className={p.color}>
                                                {i > 0 && <span className="text-gray-400 mx-0.5">/</span>}
                                                {p.label}
                                            </span>
                                        ))}
                                    </span>
                                );
                            })()}
                        </div>
                        <div className="overflow-x-auto rounded-lg shadow">
                            {(() => {
                                const displayColumns = [
                                    'Target_Chembl_ID',
                                    'Target_Name',
                                    'Uniprot_ID',
                                    'Uniprot_Accession',
                                    'Z_score',
                                    'p_value',
                                    'MaxTc',
                                    'affinity_pred_value',
                                    'affinity_probability_binary',
                                ];
                                const colLabels = {
                                    Target_Chembl_ID: 'ChEMBL ID',
                                    Target_Name: 'Target',
                                    Uniprot_ID: 'UniProt ID',
                                    Uniprot_Accession: 'UniProt Acc.',
                                    Z_score: 'Z-score',
                                    p_value: 'p-value',
                                    MaxTc: 'MaxTc',
                                    affinity_pred_value: 'ΔG pred',
                                    affinity_probability_binary: 'Bind prob.',
                                };
                                const columns = displayColumns.filter((col) => col in result[0]);
                                // chembl_id -> set of done ligand names for that target
                                const boltzDoneByChembl = Object.values(boltzJobs).reduce((acc, info) => {
                                    if (info.status === 'done' && info.chembl_id) {
                                        if (!acc[info.chembl_id]) acc[info.chembl_id] = [];
                                        acc[info.chembl_id].push(info.ligand);
                                    }
                                    return acc;
                                }, {});
                                const showBoltzCol = Object.keys(boltzDoneByChembl).length > 0;
                                return (
                                    <table className="min-w-full divide-y divide-gray-200 bg-white">
                                        <thead className="bg-blue-100">
                                            <tr>
                                                {columns.map((key) => (
                                                    <th
                                                        key={key}
                                                        onClick={() => SORTABLE_RESULT_COLS.has(key) && handleResultSort(key)}
                                                        title={key}
                                                        className={`px-2 py-2 sm:px-4 text-left text-xs sm:text-sm font-bold text-blue-700 uppercase tracking-wider border-b ${SORTABLE_RESULT_COLS.has(key) ? 'cursor-pointer select-none hover:bg-blue-200' : ''}`}
                                                    >
                                                        {colLabels[key] ?? key}{SORTABLE_RESULT_COLS.has(key) && (
                                                            <span className="ml-1" style={{ opacity: resultSortBy === key ? 1 : 0.3 }}>
                                                                {resultSortBy === key ? (resultSortDir === 'desc' ? '▼' : '▲') : '▼'}
                                                            </span>
                                                        )}
                                                    </th>
                                                ))}
                                                {showBoltzCol && (
                                                    <th className="px-2 py-2 sm:px-4 text-left text-xs sm:text-sm font-bold text-blue-700 uppercase tracking-wider border-b">
                                                        Boltz-2
                                                    </th>
                                                )}
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {sortedResult.map((item, index) => {
                                                const chemblId = item['Target_Chembl_ID'];
                                                const doneLigands = boltzDoneByChembl[chemblId];
                                                return (
                                                <tr
                                                    key={index}
                                                    className={index % 2 === 0 ? "bg-blue-50" : "bg-white"}
                                                >
                                                    {columns.map((col) => (
                                                        <td
                                                            key={col}
                                                            className="px-2 py-2 sm:px-4 text-xs sm:text-sm text-gray-700 border-b"
                                                        >
                                                            {col === 'Target_Chembl_ID' ? (
                                                                <a
                                                                    href={`/result?taskId=${taskId}&targetChemblId=${item[col]}`}
                                                                    className="text-blue-600 hover:text-blue-800 underline"
                                                                >
                                                                    {item[col]}
                                                                </a>
                                                            ) : col === 'p_value' && typeof item[col] === 'number' ? (
                                                                item[col].toExponential(4)
                                                            ) : col === 'Uniprot_ID' ? (
                                                                item[col]
                                                                    ? <a href={`https://chembl.docking.org/dataset/30uM/${item[col]}.smi`} target="_blank" rel="noreferrer" className="text-blue-600 hover:text-blue-800 underline">{item[col]}</a>
                                                                    : '—'
                                                            ) : col === 'affinity_pred_value' ? (
                                                                item[col] != null ? (typeof item[col] === 'number' ? item[col].toFixed(3) : item[col]) : '—'
                                                            ) : col === 'affinity_probability_binary' ? (
                                                                item[col] != null ? (typeof item[col] === 'number' ? `${(item[col] * 100).toFixed(1)}%` : item[col]) : '—'
                                                            ) : (
                                                                item[col]
                                                            )}
                                                        </td>
                                                    ))}
                                                    {showBoltzCol && (
                                                        <td className="px-2 py-2 sm:px-4 text-xs sm:text-sm border-b">
                                                            {doneLigands && doneLigands.length > 0 ? (
                                                                <a
                                                                    href={`/api/boltz_download?task_id=${taskId}&chembl_id=${chemblId}`}
                                                                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-700 hover:bg-indigo-200 whitespace-nowrap"
                                                                    title={`Download Boltz-2 structures for ${chemblId}`}
                                                                >
                                                                    ↓ Result
                                                                </a>
                                                            ) : (
                                                                <span className="text-gray-300">—</span>
                                                            )}
                                                        </td>
                                                    )}
                                                </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                );
                            })()}
                        </div>
                    </div>
                )}
            </div>
        </div>
        </Layout>
    );
}