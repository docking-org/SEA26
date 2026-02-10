import React, { useState, useEffect, useMemo } from 'react';
import { useParams, useSearchParams, Link } from 'react-router-dom';
import Layout from './components/Layout';


export default function Result() {
    const [searchParams] = useSearchParams();
    const taskId = searchParams.get('taskId') || '';
    const targetChemblId = searchParams.get('targetChemblId') || '';
    const [percent1Done, setPercent1Done] = useState(0);
    const [result, setResult] = useState([]);
    const [queryCompounds, setQueryCompounds] = useState([]);
    const [filteredData, setFilteredData] = useState([]);
    const [heatmapData, setHeatmapData] = useState(null);
    const [seaData, setSeaData] = useState(null);
    const [hoveredCell, setHoveredCell] = useState(null);
    const [sortBy, setSortBy] = useState('score');
    const [activeButton, setActiveButton] = useState(false);

    // RDkit images
    const [rdkitReady, setRdkitReady] = useState(false);
    const [moleculeImages, setMoleculeImages] = useState({});

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

            const svg = mol.get_svg(width, height);
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
                    generateMoleculeSVG(item.Known_Hit_SMILES, item.Known_Hit_ID);
                }
            });
        }
    }, [heatmapData, rdkitReady]);

    async function checkTaskStatus(){
        try {
            const response = await fetch(`/api/result?taskId=${taskId}`);
            const data = await response.json();
            
            if(data.status === 'SUCCESS'){
                setPercent1Done(100);
                setResult(data.result);
                console.log('Task completed:', data);
            }
            else if (data.status === 'PROGRESS'){
                setPercent1Done(data.progress);
                console.log('Task in progress:', data);
                setTimeout(checkTaskStatus, 500); 
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




    // Memoized sorted data
    const sortedData = useMemo(() => {
        if (!heatmapData|| heatmapData.length === 0) return [];
        const sorted = [...heatmapData];
        if (sortBy === 'score') {
            sorted.sort((a, b) => b.Tanimoto_Score - a.Tanimoto_Score);
        } else {
            sorted.sort((a, b) => a.Known_Hit_ID.localeCompare(b.Known_Hit_ID));
        }
        return sorted;
    }, [heatmapData, sortBy]);

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

    // Initiate task status check
    useEffect(() => {
        checkTaskStatus();
    }, [taskId]);

    // Fetch heatmap data when targetChemblId changes
    useEffect(() => {
        if (targetChemblId) {
            fetchTargetHeatMap(targetChemblId, taskId);
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
            <div className="min-h-screen bg-gray-100 p-8">
                <div className="max-w-7xl mx-auto">
                    <Link 
                        to={`/result?taskId=${taskId}`}
                        className="text-blue-600 hover:underline mb-4 inline-block"
                    >
                        ← Back to all results
                    </Link>

                    <div className="bg-white rounded-lg shadow-lg p-6 mt-4">
                     
                        <div className="flex flex-col md:flex-row md:justify-between md:items-center mb-4 gap-4">
                            <div>
                                <h2 className="text-2xl font-bold text-gray-800">Tanimoto Similarity Results</h2>
                                <div className="flex flex-col md:flex-row md:items-center mb-6 gap-4">
                                    <p className="text-sm text-gray-600 mr-4">Query Compound:</p>
                                    <select
                                        className="px-3 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium"
                                        onChange={(e) => {
                                        const selectedCompound = e.target.value;
                                        if (selectedCompound === "all") {
                                            handleHeatMapDisplay(null); // Display all compounds
                                        } else {
                                            handleHeatMapDisplay(selectedCompound); // Display selected compound
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
                                <p className="text-sm text-gray-600">Target: {targetChemblId}</p>
                            </div>
                            <div className="flex gap-2">
                                <button
                                    onClick={() => setSortBy('score')}
                                    className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
                                        sortBy === 'score' 
                                            ? 'bg-blue-600 text-white' 
                                            : 'bg-gray-200 text-gray-700 hover:bg-gray-300'
                                    }`}
                                >
                                    Sort by Score
                                </button>
                                <button
                                    onClick={() => setSortBy('id')}
                                    className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
                                        sortBy === 'id' 
                                            ? 'bg-blue-600 text-white' 
                                            : 'bg-gray-200 text-gray-700 hover:bg-gray-300'
                                    }`}
                                >
                                    Sort by ID
                                </button>
                            </div>
                        </div>
                        
                        <div className="overflow-x-auto">
                            <table className="w-full border-collapse">
                                <thead>
                                    <tr>
                                        <th className="p-3 text-left text-sm font-semibold text-gray-700 bg-gray-100 sticky left-0 z-10">
                                            Known Hit ID
                                        </th>
                                        <th className="p-3 text-center text-sm font-semibold text-gray-700 bg-gray-100">
                                            Tanimoto Score
                                        </th>
                                        <th className="p-3 text-left text-sm font-semibold text-gray-700 bg-gray-100">
                                            SMILES
                                        </th>
                                        <th className="p-3 text-left text-sm font-semibold text-gray-700 bg-gray-100">
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
                                            <td className="p-3 text-sm font-medium text-gray-900 sticky left-0 bg-white">
                                                {item.Known_Hit_ID}
                                            </td>
                                            <td 
                                                className="relative p-0"
                                                onMouseEnter={() => setHoveredCell(index)}
                                                onMouseLeave={() => setHoveredCell(null)}
                                            >
                                                <div
                                                    className="h-16 flex items-center justify-center text-sm font-bold cursor-pointer transition-transform hover:scale-105"
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
                                            <td className="p-3 text-xs text-gray-600 font-mono max-w-md truncate" title={item.Known_Hit_SMILES}>
                                                {item.Known_Hit_SMILES}
                                            </td>
                                            <td className="p-3">
                                                {moleculeImages[item.Known_Hit_ID] ? (
                                                    <div
                                                        className="w-48 h-48"
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
                                                    className="w-12 h-8 border border-gray-300 flex items-center justify-center text-xs font-semibold"
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
            </div>
            </Layout>
        );
    }

    // Main results page
    return (
        <Layout>
        <div className="flex-col items-center justify-center min-h-screen py-2 text-gray-700">
            <div className="mt-5 px-10 py-5 bg-gray-50 " >
                <h1 className="text-2xl  font-bold mb-4">Result Page</h1>
                <p className="mb-2">Your task ID is: {taskId}</p>
                
                {/* Progress Bar */}
                <div className="mb-6">
                    <div className="flex justify-between items-center mb-2">
                        <span className="text-sm font-medium text-gray-700">Processing Status</span>
                        <span className="text-sm font-medium text-gray-700">{percent1Done}%</span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-4 overflow-hidden shadow-inner">
                        <div
                            className="h-full rounded-full transition-all duration-500 ease-out flex items-center justify-end pr-2"
                            style={{
                                width: `${percent1Done}%`,
                                backgroundColor: percent1Done === 100 ? '#10b981' : '#3b82f6'
                            }}
                        >
                            {percent1Done > 10 && (
                                <span className="text-xs font-bold text-white">
                                    {percent1Done === 100 ? '✓' : ''}
                                </span>
                            )}
                        </div>
                    </div>
                    <p className="text-xs text-gray-500 mt-1">
                        {percent1Done === 100 ? 'Task completed successfully!' : 'Please wait while we process your request...'}
                    </p>
                </div>
           
                {percent1Done === 100 && result.length > 0 && (
                    <div>
                        <a
                            href={`/api/download?taskId=${taskId}`}
                            className="inline-block my-4 px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 font-bold no-underline"
                            download
                        >
                            Download Results (ZIP)
                        </a>
                        &nbsp;
                        <a 
                            href={`/compoundVStarget?taskId=${taskId}`} 
                            className="inline-block my-4 px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 font-bold no-underline"
                        >
                            Compound VS Targets
                        </a>
                        <h2 className="mt-6 mb-2 text-xl font-semibold">Results:</h2>
                        <div className="overflow-x-auto rounded-lg shadow">
                            <table className="min-w-full divide-y divide-gray-200 bg-white">
                                <thead className="bg-blue-100">
                                    <tr>
                                        {Object.keys(result[0]).map((key) => (
                                            <th
                                                key={key}
                                                className="px-4 py-2 text-left text-sm font-bold text-blue-700 uppercase tracking-wider border-b"
                                            >
                                                {key}
                                            </th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {result.map((item, index) => (
                                        <tr
                                            key={index}
                                            className={index % 2 === 0 ? "bg-blue-50" : "bg-white"}
                                        >
                                            {Object.values(item).map((value, i) => (
                                                <td
                                                    key={i}
                                                    className="px-4 py-2 text-sm text-gray-700 border-b"
                                                >
                                                    {i === 0 ? (
                                                        <a 
                                                            href={`/result?taskId=${taskId}&targetChemblId=${value}`} 
                                                            className="text-blue-600 hover:text-blue-800 underline"
                                                            target='_blank'
                                                            rel="noopener noreferrer"
                                                        >
                                                            {value}
                                                        </a>
                                                    ) : (
                                                        value
                                                    )}
                                                </td>
                                            ))}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}
            </div>
        </div>
        </Layout>
    );
}