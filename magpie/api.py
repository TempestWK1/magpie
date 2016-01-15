from __future__ import division, unicode_literals

import os
import time

import numpy as np
import pandas as pd

from magpie.base.document import Document
from magpie.base.inverted_index import InvertedIndex
from magpie.base.model import LearningModel
from magpie.candidates import generate_keyword_candidates
from magpie.candidates.utils import add_gt_answers_to_candidates_set
from magpie.config import MODEL_PATH, HEP_TRAIN_PATH, HEP_ONTOLOGY, \
    HEP_TEST_PATH
from magpie.evaluation.standard_evaluation import evaluate_results
from magpie.evaluation.utils import remove_unguessable_answers
from magpie.feature_extraction import preallocate_feature_matrix
from magpie.feature_extraction.document_features import \
    extract_document_features
from magpie.feature_extraction.keyword_features import extract_keyword_features, \
    rebuild_feature_matrix
from magpie.misc.utils import save_to_disk, load_from_disk
from magpie.utils import get_ontology, get_answers_for_doc, get_documents


def extract(
    path_to_file,
    ontology_path=HEP_ONTOLOGY,
    model_path=MODEL_PATH,
    recreate_ontology=False,
    verbose=False,
):
    """
    Extract keywords from a given file
    :param path_to_file: unicode with the filepath
    :param ontology_path: unicode with the ontology path
    :param model_path: unicode with the trained model path
    :param recreate_ontology: boolean flag whether to recreate the ontology
    :param verbose: whether to print additional info

    :return: set of predicted keywords
    """
    doc = Document(0, path_to_file)
    ontology = get_ontology(path=ontology_path, recreate=recreate_ontology)
    inv_index = InvertedIndex(doc)

    # Load the model
    model = load_from_disk(model_path)

    # Generate keyword candidates
    kw_candidates = list(generate_keyword_candidates(doc, ontology))

    X = preallocate_feature_matrix(len(kw_candidates))
    # Extract features for keywords
    extract_keyword_features(
        kw_candidates,
        X,
        inv_index,
        model,
    )

    # Extract document features
    extract_document_features(inv_index, X)

    X = rebuild_feature_matrix(X)

    # Predict
    y_predicted = model.scale_and_predict(X)

    kw_predicted = []
    for bit, kw in zip(y_predicted, kw_candidates):
        if bit == 1:
            kw_predicted.append(kw)

    # Print results
    if verbose:
        print("Document content:")
        print doc

        print("Predicted keywords:")
        for kw in kw_predicted:
            print(u"\t" + unicode(kw.get_canonical_form()))
        print

        answers = get_answers_for_doc(doc.filename, os.path.dirname(doc.filepath))
        answers = remove_unguessable_answers(answers, ontology)

        candidates = {kw.get_canonical_form() for kw in kw_candidates}
        print("Ground truth keywords:")
        for kw in answers:
            in_candidates = "(in candidates)" if kw in candidates else ""
            print("\t" + kw.ljust(30, ' ') + in_candidates)
        print

        y = []
        for kw in kw_candidates:
            y.append(1 if kw.get_canonical_form() in answers else 0)

        X['name'] = [kw.get_canonical_form() for kw in kw_candidates]
        X['predicted'] = y_predicted
        X['ground truth'] = y

        pd.set_option('expand_frame_repr', False)
        X = X[['name', 'predicted', 'ground truth', 'tf', 'idf', 'tfidf',
               'first_occurrence', 'last_occurrence', 'spread',
               'hops_from_anchor', 'no_of_letters', 'no_of_words']]
        print X[(X['ground truth'] == 1) | (X['predicted'])]

    return {kw.get_canonical_form() for kw in kw_predicted}


def test(
    testset_path=HEP_TEST_PATH,
    ontology_path=HEP_ONTOLOGY,
    model_path=MODEL_PATH,
    recreate_ontology=False,
    verbose=True,
):
    """
    Test the trained model on a set under a given path.
    :param testset_path: path to the directory with the test set
    :param ontology_path: path to the ontology
    :param model_path: path where the model is pickled
    :param recreate_ontology: boolean flag whether to recreate the ontology
    :param verbose: whether to print computation times

    :return tuple of four floats (precision, recall, f1_score, accuracy)
    """
    ontology = get_ontology(path=ontology_path, recreate=recreate_ontology)

    # Load the model
    model = load_from_disk(model_path)

    feature_matrices = []
    kw_vector = []
    answers = dict()

    cand_gen_time = feature_ext_time = 0

    for doc in get_documents(testset_path):
        inv_index = InvertedIndex(doc)
        candidates_start = time.clock()

        # Generate keyword candidates
        kw_candidates = list(generate_keyword_candidates(doc, ontology))

        candidates_end = time.clock()

        # Preallocate the feature matrix
        X = preallocate_feature_matrix(len(kw_candidates))

        # Extract features for keywords
        extract_keyword_features(
            kw_candidates,
            X,
            inv_index,
            model,
        )

        # Extract document features
        extract_document_features(inv_index, X)

        features_end = time.clock()

        # Get ground truth answers
        answers[doc.doc_id] = get_answers_for_doc(doc.filename, testset_path)

        X = rebuild_feature_matrix(X)
        feature_matrices.append(X)

        kw_vector.extend([(doc.doc_id, kw) for kw in kw_candidates])

        cand_gen_time += candidates_end - candidates_start
        feature_ext_time += features_end - candidates_end

    # Merge feature matrices from different documents
    X = pd.concat(feature_matrices)

    if verbose:
        print("Candidate generation: {0:.2f}s".format(cand_gen_time))
        print("Feature extraction: {0:.2f}s".format(feature_ext_time))

    features_time = time.clock()

    # Predict
    y_predicted = model.scale_and_predict(X)

    if verbose:
        print("Prediction time: {0:.2f}s".format(time.clock() - features_time))

    # Remove ground truth answers that are not in the ontology
    for doc_id, kw_set in answers.items():
        answers[doc_id] = remove_unguessable_answers(kw_set, ontology)

    # Evaluate the results
    precision, recall, accuracy = evaluate_results(
        y_predicted,
        kw_vector,
        answers,
    )

    f1_score = (2 * precision * recall) / (precision + recall)
    return precision, recall, f1_score, accuracy


def train(
    trainset_dir=HEP_TRAIN_PATH,
    ontology_path=HEP_ONTOLOGY,
    model_path=MODEL_PATH,
    recreate_ontology=False,
    verbose=True,
):
    """
    Train and save the model on a given dataset
    :param trainset_dir: path to the directory with the training set
    :param ontology_path:
    :param model_path:
    :param recreate_ontology: boolean flag whether to recreate the ontology
    :param verbose: whether to print computation times

    :return None if everything goes fine, error otherwise
    """
    ontology = get_ontology(path=ontology_path, recreate=recreate_ontology)
    docs = get_documents(trainset_dir, as_generator=False)

    t_start = time.clock()
    model = LearningModel(docs=docs)
    if verbose:
        print("Building the model: {0:.2f}s".format(time.clock() - t_start))

    output_vectors = []
    feature_matrices = []

    cand_gen_time = feature_ext_time = 0

    for doc in docs:
        inv_index = InvertedIndex(doc)
        candidates_start = time.clock()

        # Generate keyword candidates
        kw_candidates = list(generate_keyword_candidates(doc, ontology))

        # Get ground truth answers
        doc_answers = get_answers_for_doc(doc.filename, trainset_dir)

        # If an answer was not generated, add it anyway
        add_gt_answers_to_candidates_set(kw_candidates, doc_answers, ontology)

        candidates_end = time.clock()

        # Preallocate the feature matrix
        X = preallocate_feature_matrix(len(kw_candidates))

        # Extract features for keywords
        extract_keyword_features(
            kw_candidates,
            X,
            inv_index,
            model,
        )

        # Extract document features
        extract_document_features(inv_index, X)

        X = rebuild_feature_matrix(X)
        feature_matrices.append(X)

        features_end = time.clock()

        # Create the output vector
        # TODO this vector is very sparse, we can make it more memory efficient
        output_vector = []
        for kw in kw_candidates:
            if kw.get_canonical_form() in doc_answers:
                output_vector.append(1)  # True
            else:
                output_vector.append(0)  # False

        # feature_matrices.append(feature_matrix)
        output_vectors.extend(output_vector)

        cand_gen_time += candidates_end - candidates_start
        feature_ext_time += features_end - candidates_end

    # Merge the pandas
    X = pd.concat(feature_matrices)

    # Cast the output vector to numpy
    y = np.array(output_vectors)

    if verbose:
        print("Candidate generation: {0:.2f}s".format(cand_gen_time))
        print("Feature extraction: {0:.2f}s".format(feature_ext_time))
    t1 = time.clock()

    if verbose:
        print("X size: {}".format(X.shape))

    # Normalize features
    X = model.fit_and_scale(X)

    # Train the model
    model.fit_classifier(X, y)

    if verbose:
        print("Fitting the model: {0:.2f}s".format(time.clock() - t1))

    # Pickle the model
    save_to_disk(model_path, model, overwrite=True)


if __name__ == '__main__':
    train()