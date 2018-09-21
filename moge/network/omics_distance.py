
import numpy as np
import pandas as pd
from Bio import pairwise2
from TCGAMultiOmics.multiomics import MultiOmicsData
from scipy.spatial.distance import pdist as scipy_pdist
from scipy.spatial.distance import squareform as squareform_
from sklearn.metrics.pairwise import pairwise_distances


def compute_expression_correlations(multi_omics_data: MultiOmicsData, modalities, node_list, pathologic_stages=[],
                                    histological_subtypes=[]):
    X_multiomics, y = multi_omics_data.load_data(modalities=modalities, pathologic_stages=pathologic_stages,
                                                 histological_subtypes=histological_subtypes)

    X_multiomics_concat = pd.concat([X_multiomics[m] for m in modalities], axis=1)
    X_multiomics_corr = pairwise_distances(X_multiomics_concat.T, metric='correlation')

    cols = X_multiomics_concat.columns
    X_multiomics_corr_df = pd.DataFrame(X_multiomics_corr, columns=cols, index=cols)
    print(X_multiomics_corr_df.shape)
    X_multiomics_corr_df = X_multiomics_corr_df.filter(items=node_list)
    X_multiomics_corr_df = X_multiomics_corr_df.filter(items=node_list, axis=0)

    print(X_multiomics_corr_df.shape)

    return X_multiomics_corr_df


def compute_annotation_similarity(genes_info, node_list, modality, features=None, squareform=True,
                                  multiprocessing=True):
    if features is None:
        if modality == "GE":
            features = ["locus_type", "gene_family_id", "location", "Transcript length", "Transcript sequence"]
        elif modality == "MIR":
            features = ["miR family", "location", "Mature sequence"]
        elif modality == "LNC":
            features = ["Transcript Type", "Location", "Transcript length", "Transcript sequence"]

    gower_dists = gower_distance(genes_info.loc[node_list, features], agg_func=None, multiprocessing=multiprocessing)

    if squareform:
        return squareform_(np.subtract(1, gower_dists))
    else:
        return np.subtract(1, gower_dists) # Turns distance to similarity measure
    # return np.exp(-beta * gower_dists)


def gower_distance(X, agg_func=None, multiprocessing=True, n_jobs=-2):
    """
    This function expects a pandas dataframe as input
    The data frame is to contain the features along the columns. Based on these features a
    distance matrix will be returned which will contain the pairwise gower distance between the rows
    All variables of object type will be treated as nominal variables and the others will be treated as
    numeric variables.
    Distance metrics used for:
    Nominal variables: Dice distance (https://en.wikipedia.org/wiki/S%C3%B8rensen%E2%80%93Dice_coefficient)
    Numeric variables: Manhattan distance normalized by the range of the variable (https://en.wikipedia.org/wiki/Taxicab_geometry)
    """
    individual_variable_distances = []

    if multiprocessing:
        pdist = lambda X, metric: squareform_(pairwise_distances(X=X, metric=metric, n_jobs=n_jobs), checks=False)
    else:
        pdist = scipy_pdist # returns condensed dist matrix

    for column in X.columns:
        feature = X.loc[:, column]
        print("Gower's dissimilarity: Computing", column, ", dtype:", feature.dtypes, ", shape:", feature.shape)

        if column in ["gene_family_id", "gene_family", "locus_type"]:
            print("Dice distance")
            feature_dist = pdist(feature.str.get_dummies("|"), 'dice')

        elif column == "miR family":
            print("Dice distance")
            feature_dist = pdist(feature.str.get_dummies("/"), 'dice')

        elif column == "GO terms":
            print("Dice distance")
            feature_dist = pdist(feature.str.get_dummies(","), 'dice')

        elif column in ["Mature sequence", "Transcript sequence"]:
            print("Global alignment seq score")
            feature_dist = pdist(feature.values.reshape((X.shape[0], -1)), seq_global_alignment_pairwise_score)
            feature_dist = 1-feature_dist # Convert from similarity to dissimilarity

        elif column == "Location": # LNC Locations
            print("Location split to Chromosome, start, end")
            location_features = feature.str.split("[:-]", expand=True).filter(items=[0, 1])
            hierarchical_columns = ["Chromosome", "start"]
            location_features.columns = hierarchical_columns
            location_features["start"] = location_features["start"].astype(np.float64)
            # location_features["end"] = location_features["end"].astype(np.float64) TODO Add bp region length

            feature_dist = gower_distance(location_features, agg_func=hierarchical_distance_aggregate_score,
                                          multiprocessing=True)

        elif column == "location": # GE Locations
            print("Location split to Chromosome, arm, region")
            location_features = feature.str.split("[pq.]", expand=True).filter(items=[0, 1])
            location_features.columns = ["Chromosome", "region"]
            location_features["arm"] = feature.str.extract(r'(?P<arm>[pq])', expand=True)
            location_features = location_features[["Chromosome", "arm", "region"]] # TODO Add band #
            # print(location_features)
            feature_dist = gower_distance(location_features, agg_func=hierarchical_distance_aggregate_score,
                                          multiprocessing=True)

        elif feature.dtypes == np.object: # TODO Use Categorical dtypes later
            print("Dice distance")
            feature_dist = pdist(pd.get_dummies(feature), 'dice')

        elif feature.dtypes == int:
            print("Manhattan distance (normalized ptp)")
            feature_dist = scipy_pdist(feature.values.reshape((X.shape[0],-1)), "manhattan") / \
                           (np.nanmax(feature.values) - np.nanmin(feature.values))
        elif feature.dtypes == float:
            print("Euclidean distance (normalized ptp)")
            feature_dist = scipy_pdist(feature.values.reshape((X.shape[0],-1)), "euclidean") / \
                           (np.nanmax(feature.values) - np.nanmin(feature.values))
        else:
            raise Exception("Invalid column dtype")

        individual_variable_distances.append(feature_dist)

    if agg_func is None:
        agg_func = lambda x: np.nanmean(x, axis=0)

    pdists_mean_reduced = agg_func(np.array(individual_variable_distances))

    return pdists_mean_reduced

def hierarchical_distance_aggregate_score(X):
    """
    X: ndarray of features where the first dimension is ordered hierarchically (e.g. [Chromosome #, arm, region, band])
    """
    for i in range(1, len(X)):
        X[i][np.where(X[i-1] >= X[i])] = X[i-1][np.where(X[i-1] >= X[i])] # the distance of child feature is only as great as distance of parent features

    return np.nanmean(X, axis=0)


def seq_global_alignment_pairwise_score(u, v, truncate=True, min_length=600):
    if (type(u[0]) is str and type(v[0]) is str):
        if truncate and (len(u[0]) > min_length or len(v[0]) > min_length):
            return np.nan
        return pairwise2.align.globalxx(u[0], v[0], score_only=True) / min(len(u[0]), len(v[0]))
    else:
        return np.nan


